import json
import os
import argparse
import sys
from datetime import datetime
import requests
import re
from collections import deque, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Try to load configs if running standalone
try:
    from nc_zabbix_config_mgmt.config import load_config
    from nc_zabbix_config_mgmt_source.config import load_source_config
    CONFIGS_AVAILABLE = True
except ImportError:
    CONFIGS_AVAILABLE = False


def dbg(msg):
    print(f"[DEBUG] {msg}")


class ZabbixDashboardExporter:
    """Class to handle Zabbix dashboard exports with configurable parameters."""
    
    def __init__(self, url, username, password, ca_certificate_path='', 
                 export_format='json', export_dir='exports', dashboard_name=None,
                 dashboard_id=None, file_prefix='dashboard_', pretty_json_indent=2,
                 max_threads=5, request_timeout=30, map_batch_size=10,
                 include_submaps=True, include_upper_maps=True, include_templates=True,
                 include_triggers=True, include_graphs=True, include_hosts=True,
                 regex_options=None, process_aliases=None, process_to_key=None,
                 cache_to_key=None):
        
        self.ZABBIX_URL = url
        self.USERNAME = username
        self.PASSWORD = password
        self.CA_CERTIFICATE_PATH = ca_certificate_path or False
        
        self.EXPORT_FORMAT = export_format
        self.EXPORT_DIR = export_dir
        self.DASHBOARD_NAME = dashboard_name
        self.DASHBOARD_ID_CFG = dashboard_id
        
        self.FILE_PREFIX = file_prefix
        self.PRETTY_JSON_INDENT = pretty_json_indent
        
        self.MAX_THREADS = max_threads
        self.REQUEST_TIMEOUT = request_timeout
        self.MAP_BATCH_SIZE = map_batch_size
        
        self.INCLUDE_SUBMAPS = include_submaps
        self.INCLUDE_UPPER_MAPS = include_upper_maps
        self.INCLUDE_TEMPLATES = include_templates
        self.INCLUDE_TRIGGERS = include_triggers
        self.INCLUDE_GRAPHS = include_graphs
        self.INCLUDE_HOSTS = include_hosts
        
        regex_opts = regex_options or {}
        CASE_INSENSITIVE = regex_opts.get("case_insensitive", False)
        self.regex_flags = re.IGNORECASE if CASE_INSENSITIVE else 0
        
        self.PROCESS_ALIASES = {str(k).lower(): str(v).lower() for k, v in (process_aliases or {}).items()}
        self.PROCESS_TO_KEY = {str(k).lower(): str(v) for k, v in (process_to_key or {}).items()}
        self.CACHE_TO_KEY = {str(k).lower(): str(v) for k, v in (cache_to_key or {}).items()}
        
        self.RE_UTILITY_MAX = re.compile(r'(?:\d+)?\s*ZABBIX\.SERVER\.UTIL\.MAX\s*(?::)?\s*(?:\"([^\"]+)\"|([^\{\s}][^}\s]*))', self.regex_flags)
        self.RE_UTILITY_MIN = re.compile(r'(?:\d+)?\s*ZABBIX\.SERVER\.UTIL\.MIN\s*(?::)?\s*(?:\"([^\"]+)\"|([^\{\s}][^}\s]*))', self.regex_flags)

    def _post(self, payload, timeout=None):
        if timeout is None:
            timeout = self.REQUEST_TIMEOUT
        dbg(f"POST {payload.get('method')} id={payload.get('id')} params_keys={list((payload.get('params') or {}).keys())}")
        r = requests.post(self.ZABBIX_URL, json=payload, verify=self.CA_CERTIFICATE_PATH, timeout=timeout)
        try:
            j = r.json()
        except Exception:
            dbg(f"HTTP status={r.status_code} text={r.text[:500]}")
            raise
        if "error" in j:
            dbg(f"API error for {payload.get('method')}: {j['error']}")
        return j

    def login_to_zabbix(self):
        payload = {"jsonrpc": "2.0", "method": "user.login", "params": {"username": self.USERNAME, "password": self.PASSWORD}, "auth": None, "id": 1}
        resp = self._post(payload)
        token = resp.get("result")
        dbg(f"Logged in: token_present={bool(token)}")
        return token

    def logout_from_zabbix(self, auth_token):
        payload = {"jsonrpc": "2.0", "method": "user.logout", "params": [], "auth": auth_token, "id": 2}
        try:
            self._post(payload)
            dbg("Logged out")
        except Exception as e:
            dbg(f"Logout failed: {e}")

    def configuration_export(self, auth, *, hosts=None, templates=None, groups=None, maps=None, format_="json"):
        options = {}
        if hosts:
            options["hosts"] = [str(h) for h in hosts]
        if templates:
            options["templates"] = [str(t) for t in templates]
        if groups:
            options["groups"] = [str(g) for g in groups]
        if maps:
            options["maps"] = [str(m) for m in maps]
        dbg(f"configuration.export options: hosts={len(options.get('hosts',[]))} templates={len(options.get('templates',[]))} groups={len(options.get('groups',[]))} maps={len(options.get('maps',[]))} format={format_}")
        payload = {"jsonrpc": "2.0", "method": "configuration.export", "params": {"options": options, "format": format_}, "auth": auth, "id": 41}
        resp = self._post(payload)
        result = resp.get("result", "")
        dbg(f"configuration.export length={len(result)}")
        return result

    def normalize_process_label(self, label: str) -> str:
        key = (label or "").strip().lower()
        return self.PROCESS_ALIASES.get(key, key)

    def normalize_cache_label(self, label: str) -> str:
        return (label or "").strip().lower()

    def build_utility_expression(self, expression):
        def repl_max(m):
            label = m.group(1) or m.group(2) or ""
            norm = self.normalize_cache_label(label)
            key = self.CACHE_TO_KEY.get(norm)
            if not key:
                norm = self.normalize_process_label(label)
                key = self.PROCESS_TO_KEY.get(norm)
            if not key:
                return m.group(0)
            return f'max([/Zabbix server/{key}],10m)'
        def repl_min(m):
            label = m.group(1) or m.group(2) or ""
            norm = self.normalize_cache_label(label)
            key = self.CACHE_TO_KEY.get(norm)
            if not key:
                norm = self.normalize_process_label(label)
                key = self.PROCESS_TO_KEY.get(norm)
            if not key:
                return m.group(0)
            return f'min([/Zabbix server/{key}],10m)'
        out = self.RE_UTILITY_MAX.sub(repl_max, expression or "")
        out = self.RE_UTILITY_MIN.sub(repl_min, out)
        return out

    def map_trigger_expression(self, expression, items_map):
        util_mapped = self.build_utility_expression(expression)
        def replace_itemid(match):
            itemid = match.group(1)
            item = items_map.get(itemid)
            if not item:
                return match.group(0)
            host = item.get("host", "UNKNOWN_HOST")
            key = item.get("key_", "UNKNOWN_KEY")
            return f"/{host}/{key}"
        new_expr = re.sub(r"{(\d+)}", replace_itemid, util_mapped)
        return new_expr

    def get_dashboard_id_by_name(self, auth, dashboard_name):
        payload = {"jsonrpc": "2.0", "method": "dashboard.get",
                   "params": {"filter": {"name": [dashboard_name]}, "output": ["dashboardid","name"]},
                   "auth": auth, "id": 7}
        resp = self._post(payload)
        lst = resp.get("result", []) or []
        dbg(f"dashboard.get by name='{dashboard_name}' found={len(lst)}")
        return lst[0]["dashboardid"] if lst else None

    def get_all_maps_basic(self, auth):
        payload = {"jsonrpc": "2.0", "method": "map.get", "params": {"output": "extend"}, "auth": auth, "id": 1001}
        resp = self._post(payload)
        maps = resp.get("result", []) or []
        dbg(f"map.get basic count={len(maps)}")
        return maps

    def get_maps_by_ids(self, auth, ids):
        if not ids:
            return []
        ids = [str(i) for i in ids]
        dbg(f"map.get by ids count={len(ids)}")
        collected = {}
        batches = [ids[i:i+self.MAP_BATCH_SIZE] for i in range(0, len(ids), self.MAP_BATCH_SIZE)]
        def fetch_batch(batch):
            payload = {"jsonrpc": "2.0", "method": "map.get",
                       "params": {"mapids": batch, "output": "extend", "selectSelements": "extend", "selectLinks": "extend"},
                       "auth": auth, "id": 1002}
            r = requests.post(self.ZABBIX_URL, json=payload, verify=self.CA_CERTIFICATE_PATH, timeout=self.REQUEST_TIMEOUT).json()
            for m in r.get("result", []) or []:
                mid = str(m.get("sysmapid") or m.get("mapid") or m.get("id"))
                collected[mid] = m
            dbg(f"batch fetched maps={len(r.get('result', []) or [])}")
        with ThreadPoolExecutor(max_workers=self.MAX_THREADS) as ex:
            futs = [ex.submit(fetch_batch, b) for b in batches]
            for _ in as_completed(futs):
                pass
        dbg(f"total maps fetched by ids={len(collected)}")
        return list(collected.values())

    def build_submap_edges(self, all_maps_full):
        edges = defaultdict(list)
        reverse_edges = defaultdict(list)
        total_links = 0
        for m in all_maps_full:
            mid = str(m.get("sysmapid"))
            for se in m.get("selements", []) or []:
                et = int(se.get("elementtype", se.get("selementtype", 0)) or 0)
                if et == 3:
                    submap_id = None
                    if se.get("elements"):
                        e0 = se["elements"][0]
                        submap_id = e0.get("sysmapid") or e0.get("mapid")
                    if not submap_id and se.get("sysmapid"):
                        submap_id = se.get("sysmapid")
                    if submap_id:
                        smid = str(submap_id)
                        edges[mid].append(smid)
                        reverse_edges[smid].append(mid)
                        total_links += 1
        dbg(f"submap edges built: edges={sum(len(v) for v in edges.values())} reverse_edges={sum(len(v) for v in reverse_edges.values())} links={total_links}")
        return edges, reverse_edges

    def collect_related_maps(self, auth, seed_map_ids):
        if not seed_map_ids:
            dbg("collect_related_maps: empty seed, returning []")
            return []
        basic = self.get_all_maps_basic(auth)
        id_to_basic = {str(m.get("sysmapid")): m for m in basic if m.get("sysmapid")}
        seed = [str(s) for s in seed_map_ids if str(s) in id_to_basic]
        dbg(f"collect_related_maps: input_seeds={len(seed_map_ids)} normalized_seeds={len(seed)}")
        if not seed:
            dbg("collect_related_maps: no normalized seeds matched existing maps")
            return []
        needed_ids = set(seed)
        queue = deque(seed)
        visited_down = set(seed)
        visited_up = set(seed)
        while queue:
            chunk = []
            while queue and len(chunk) < self.MAP_BATCH_SIZE:
                chunk.append(queue.popleft())
            full = self.get_maps_by_ids(auth, chunk)
            edges, reverse_edges = self.build_submap_edges(full)
            if self.INCLUDE_SUBMAPS:
                down_next = []
                for src in chunk:
                    for dst in edges.get(src, []):
                        if dst not in visited_down:
                            visited_down.add(dst)
                            needed_ids.add(dst)
                            down_next.append(dst)
                for x in down_next:
                    queue.append(x)
                dbg(f"down traversal: expanded={len(down_next)} total_needed={len(needed_ids)}")
            if self.INCLUDE_UPPER_MAPS:
                up_next = []
                for src in chunk:
                    for parent in reverse_edges.get(src, []):
                        if parent not in visited_up:
                            visited_up.add(parent)
                            needed_ids.add(parent)
                            up_next.append(parent)
                for x in up_next:
                    queue.append(x)
                dbg(f"up traversal: expanded={len(up_next)} total_needed={len(needed_ids)}")
        result = self.get_maps_by_ids(auth, list(needed_ids))
        dbg(f"collect_related_maps: final_count={len(result)}")
        return result

    def enrich_maps_with_uids_and_endpoints(self, auth, maps):
        dbg(f"enrich maps: input={len(maps)}")
        for m in maps:
            m["sysmapname"] = m.get("name")
            m["map_uid"] = f"map:{m.get('sysmapid') or m.get('mapid') or m.get('id')}:{m.get('name')}"
            for idx, se in enumerate(m.get("selements", []) or []):
                et = int(se.get("elementtype", se.get("selementtype", 0)) or 0)
                x = se.get("x")
                y = se.get("y")
                lbl = se.get("label") or ""
                se["selement_uid"] = f"{m['map_uid']}:se:{et}:{x}:{y}:{lbl}"
                if not se.get("iconid_off") or str(se.get("iconid_off")) == "0":
                    if isinstance(se.get("icon_off"), dict) and se["icon_off"].get("imageid"):
                        se["iconid_off"] = se["icon_off"]["imageid"]
                if et == 0:
                    hostid = None
                    host_name = None
                    if se.get("elements"):
                        e0 = se["elements"][0]
                        hostid = e0.get("hostid")
                    if not hostid and se.get("hostid"):
                        hostid = se.get("hostid")
                    if hostid:
                        try:
                            pr = {"jsonrpc": "2.0", "method": "host.get", "params": {"hostids": [str(hostid)], "output": ["host"]}, "auth": auth, "id": 301}
                            hr = self._post(pr)
                            if hr.get("result"):
                                host_name = hr["result"][0].get("host")
                        except Exception as e:
                            dbg(f"host.get failed for hostid={hostid}: {e}")
                    if se.get("elements"):
                        se["elements"][0]["hostid"] = str(hostid) if hostid else se["elements"][0].get("hostid")
                        if host_name:
                            se["elements"][0]["host"] = host_name
                    else:
                        elem = {}
                        if hostid:
                            elem["hostid"] = str(hostid)
                        if host_name:
                            elem["host"] = host_name
                        if elem:
                            se["elements"] = [elem]
                elif et == 1:
                    groupid = None
                    group_name = None
                    if se.get("elements"):
                        e0 = se["elements"][0]
                        groupid = e0.get("groupid")
                        group_name = e0.get("name") or e0.get("group") or e0.get("group_name")
                    if not groupid and se.get("groupid"):
                        groupid = se.get("groupid")
                    if groupid and not group_name:
                        try:
                            gr = {"jsonrpc": "2.0", "method": "hostgroup.get", "params": {"groupids": [str(groupid)], "output": ["name"]}, "auth": auth, "id": 302}
                            gresp = self._post(gr)
                            if gresp.get("result"):
                                group_name = gresp["result"][0].get("name")
                        except Exception as e:
                            dbg(f"hostgroup.get failed for groupid={groupid}: {e}")
                    if se.get("elements"):
                        se["elements"][0]["groupid"] = str(groupid) if groupid else se["elements"][0].get("groupid")
                        if group_name:
                            se["elements"][0]["name"] = group_name
                    else:
                        elem = {}
                        if groupid:
                            elem["groupid"] = str(groupid)
                        if group_name:
                            elem["name"] = group_name
                        if elem:
                            se["elements"] = [elem]
                elif et == 3:
                    submap_id = None
                    submap_name = None
                    if se.get("elements"):
                        e0 = se["elements"][0]
                        submap_id = e0.get("sysmapid") or e0.get("mapid")
                        submap_name = e0.get("name") or e0.get("mapname")
                    if not submap_id and se.get("sysmapid"):
                        submap_id = se.get("sysmapid")
                    if submap_id and not submap_name:
                        try:
                            mr = {"jsonrpc": "2.0", "method": "map.get", "params": {"sysmapids": [str(submap_id)], "output": ["name"]}, "auth": auth, "id": 303}
                            mresp = self._post(mr)
                            if mresp.get("result"):
                                submap_name = mresp["result"][0].get("name")
                        except Exception as e:
                            dbg(f"map.get name lookup failed for sysmapid={submap_id}: {e}")
                    # Ergänzung: Referenzen IMMER explizit schreiben
                    if submap_id:
                        se["sysmapid"] = str(submap_id)
                    if submap_name:
                        se["sysmapname"] = submap_name
                    if se.get("elements"):
                        if submap_id:
                            se["elements"][0]["sysmapid"] = str(submap_id)
                        if submap_name:
                            se["elements"][0]["name"] = submap_name
                    else:
                        elem = {}
                        if submap_id:
                            elem["sysmapid"] = str(submap_id)
                        if submap_name:
                            elem["name"] = submap_name
                        if elem:
                            se["elements"] = [elem]
            uid_by_index = {}
            for idx, se in enumerate(m.get("selements", []) or []):
                uid_by_index[str(idx)] = se.get("selement_uid")
            for ln in (m.get("links", []) or []):
                e1 = ln.get("selementid1")
                e2 = ln.get("selementid2")
                ln["endpoint1"] = {"selement_uid": uid_by_index.get(str(e1) if isinstance(e1, int) else str(e1))} if e1 is not None else {}
                ln["endpoint2"] = {"selement_uid": uid_by_index.get(str(e2) if isinstance(e2, int) else str(e2))} if e2 is not None else {}
                def key_for(idx):
                    try:
                        se = m["selements"][int(idx)]
                        et = int(se.get("elementtype", se.get("selementtype", 0)) or 0)
                        return {"type": et, "x": se.get("x"), "y": se.get("y"), "label": se.get("label")}
                    except Exception:
                        return {}
                ln["endpoint1_key"] = key_for(e1) if e1 is not None else {}
                ln["endpoint2_key"] = key_for(e2) if e2 is not None else {}
        dbg("enrich maps done")

    def get_graphs_items_hosts(self, graph_ids, auth):
        graphs, items, hosts = [], [], {}
        if not self.INCLUDE_GRAPHS:
            dbg("skip graphs per config")
            return graphs, items, list(hosts.values())
        dbg(f"fetch graphs count={len(graph_ids)}")
        for gid in graph_ids:
            payload = {"jsonrpc": "2.0", "method": "graph.get", "params": {"graphids": [gid], "output": "extend"}, "auth": auth, "id": 11}
            gresp = self._post(payload)
            if not gresp.get("result"):
                continue
            g = gresp["result"][0]
            graphs.append(g)
            payload = {"jsonrpc": "2.0", "method": "item.get", "params": {"graphids": [gid], "output": ["itemid","key_","hostid"], "selectHosts": ["host"]}, "auth": auth, "id": 12}
            iresp = self._post(payload)
            for it in iresp.get("result", []) or []:
                it["host"] = (it.get("hosts", [{}])[0].get("host") if it.get("hosts") else None)
                items.append(it)
                hid = it.get("hostid")
                if hid and hid not in hosts:
                    ph = {"jsonrpc": "2.0", "method": "host.get", "params": {"hostids": [hid], "output": "extend"}, "auth": auth, "id": 13}
                    hresp = self._post(ph)
                    if hresp.get("result"):
                        hosts[hid] = hresp["result"][0]
        dbg(f"graphs fetched={len(graphs)} items_from_graphs={len(items)} hosts_from_graphs={len(hosts)}")
        return graphs, items, list(hosts.values())

    def get_triggers_items_hosts(self, trigger_ids, auth, existing_items=None, existing_hosts=None):
        if existing_items is None:
            existing_items = {}
        if existing_hosts is None:
            existing_hosts = {}
        triggers = []
        if not self.INCLUDE_TRIGGERS:
            dbg("skip triggers per config")
            return triggers, list(existing_items.values()), list(existing_hosts.values())
        dbg(f"fetch triggers count={len(trigger_ids)}")
        if not trigger_ids:
            return triggers, list(existing_items.values()), list(existing_hosts.values())
        payload = {"jsonrpc": "2.0", "method": "trigger.get", "params": {"triggerids": list(trigger_ids), "output": "extend", "expandExpression": 1, "selectItems": "extend"}, "auth": auth, "id": 14}
        tresp = self._post(payload)
        for trig in tresp.get("result", []) or []:
            triggers.append(trig)
            for it in trig.get("items", []) or []:
                iid = it.get("itemid")
                if iid and iid not in existing_items:
                    pi = {"jsonrpc": "2.0", "method": "item.get", "params": {"itemids": [iid], "output": ["itemid","key_","hostid"], "selectHosts": ["host"]}, "auth": auth, "id": 15}
                    iresp = self._post(pi)
                    if iresp.get("result"):
                        itm = iresp["result"][0]
                        itm["host"] = (itm.get("hosts", [{}])[0].get("host") if itm.get("hosts") else None)
                        existing_items[iid] = itm
                        hid = itm.get("hostid")
                        if hid and hid not in existing_hosts:
                            ph = {"jsonrpc": "2.0", "method": "host.get", "params": {"hostids": [hid], "output": "extend"}, "auth": auth, "id": 16}
                            hresp = self._post(ph)
                            if hresp.get("result"):
                                existing_hosts[hid] = hresp["result"][0]
        dbg(f"triggers fetched={len(triggers)} items_total={len(existing_items)} hosts_total={len(existing_hosts)}")
        return triggers, list(existing_items.values()), list(existing_hosts.values())

    def get_templates_and_groups(self, template_ids, auth):
        if not self.INCLUDE_TEMPLATES:
            dbg("skip templates per config")
            return [], []
        if not template_ids:
            return [], []
        dbg(f"fetch templates count={len(template_ids)}")
        payload = {"jsonrpc": "2.0", "method": "template.get",
                   "params": {"templateids": template_ids, "output": "extend", "selectGroups": "extend", "selectItems": "extend", "selectTriggers": "extend", "selectGraphs": "extend", "selectParentTemplates": "extend"},
                   "auth": auth, "id": 20}
        resp = self._post(payload)
        templates = resp.get("result", []) or []
        groups = {}
        for tpl in templates:
            for g in tpl.get("groups", []) or []:
                groups[g["groupid"]] = g
        dbg(f"templates fetched={len(templates)} template_groups={len(groups)}")
        return templates, list(groups.values())

    def get_host_groups_for_hosts(self, host_ids, auth):
        host_groups = {}
        if not self.INCLUDE_HOSTS:
            dbg("skip host groups per config")
            return host_groups, []
        dbg(f"fetch host groups for hosts count={len(host_ids)}")
        for hid in host_ids:
            p = {"jsonrpc": "2.0", "method": "host.get", "params": {"hostids": [hid], "output": ["hostid","host"], "selectGroups": ["groupid","name"]}, "auth": auth, "id": 19}
            r = self._post(p)
            if r.get("result"):
                host_groups[hid] = r["result"][0].get("groups", [])
        uniq = {}
        for gl in host_groups.values():
            for g in gl:
                uniq[g["groupid"]] = g
        dbg(f"unique host groups={len(uniq)}")
        return host_groups, list(uniq.values())

    def export_dashboard(self, dashboard_id, auth):
        os.makedirs(self.EXPORT_DIR, exist_ok=True)
        export_file = os.path.join(self.EXPORT_DIR, f"{self.FILE_PREFIX}{dashboard_id}.{self.EXPORT_FORMAT}")
        dbg(f"export target file={export_file}")

        pd = {"jsonrpc": "2.0", "method": "dashboard.get",
              "params": {"dashboardids": [str(dashboard_id)], "output": "extend", "selectPages": "extend", "selectUsers": "extend", "selectUserGroups": "extend"},
              "auth": auth, "id": 17}
        resp = self._post(pd)
        if not resp.get("result"):
            print(f"Dashboard {dashboard_id} not found")
            return
        dashboard = resp["result"][0]
        dbg(f"dashboard name={dashboard.get('name')} pages={len(dashboard.get('pages',[]) or [])}")

        widgets = []
        graph_ids = set()
        trigger_ids = set()
        host_ids = set()
        group_ids = set()
        seed_map_ids = set()

        for p_i, page in enumerate(dashboard.get("pages", []) or []):
            for w_i, w in enumerate(page.get("widgets", []) or []):
                widgets.append(w)
                wtype = w.get("type")
                fields = w.get("fields", []) or []
                dbg(f"page#{p_i} widget#{w_i} type={wtype} fields={len(fields)}")
                if self.INCLUDE_GRAPHS and wtype == "graph" and w.get("graphid"):
                    graph_ids.add(str(w["graphid"]))
                if self.INCLUDE_TRIGGERS and wtype == "trigger" and w.get("triggerid"):
                    trigger_ids.add(str(w["triggerid"]))
                for f in fields:
                    nm = f.get("name")
                    if nm == "sysmapid":
                        v = f.get("value")
                        if v:
                            seed_map_ids.add(str(v))
                    if self.INCLUDE_HOSTS and wtype in ("problems","problem","dataover","dataoverview","triggers","triggeroverview"):
                        if nm == "hostids":
                            v = f.get("value", [])
                            if isinstance(v, list):
                                for h in v:
                                    host_ids.add(str(h))
                            else:
                                host_ids.add(str(v))
                        if nm == "groupids":
                            v = f.get("value", [])
                            if isinstance(v, list):
                                for g in v:
                                    group_ids.add(str(g))
                            else:
                                group_ids.add(str(v))

        dbg(f"seed_map_ids from sysmapid fields count={len(seed_map_ids)}")
        all_maps_basic = self.get_all_maps_basic(auth)
        name_to_id_ci = {}
        for m in all_maps_basic:
            n = m.get("name")
            i = m.get("sysmapid")
            if n and i:
                name_to_id_ci[n.strip().lower()] = str(i)
        for page in dashboard.get("pages", []) or []:
            for w in page.get("widgets", []) or []:
                fields = w.get("fields", []) or []
                map_name = None
                for f in fields:
                    if f.get("name") in ("sysmapname", "mapname", "name"):
                        v = f.get("value")
                        if isinstance(v, str) and v.strip():
                            map_name = v.strip()
                            break
                if map_name:
                    cid = name_to_id_ci.get(map_name.lower())
                    if cid:
                        seed_map_ids.add(cid)
        dbg(f"seed_map_ids after name resolution count={len(seed_map_ids)}")

        if (self.INCLUDE_SUBMAPS or self.INCLUDE_UPPER_MAPS) and not seed_map_ids:
            dbg("no seeds from dashboard, enabling fallback: use all maps as seeds for traversal")
            seed_map_ids = {str(m.get("sysmapid")) for m in all_maps_basic if m.get("sysmapid")}
            dbg(f"fallback seed_map_ids count={len(seed_map_ids)}")

        if self.INCLUDE_SUBMAPS or self.INCLUDE_UPPER_MAPS:
            maps = self.collect_related_maps(auth, list(seed_map_ids))
        else:
            maps = self.get_maps_by_ids(auth, list(seed_map_ids))

        dbg(f"maps collected count={len(maps)}")
        self.enrich_maps_with_uids_and_endpoints(auth, maps)

        for m in maps:
            for se in (m.get("selements", []) or []):
                for el in (se.get("elements", []) or []):
                    if el.get("hostid"):
                        host_ids.add(str(el["hostid"]))
            if se := m.get("selements"):
                for se2 in se:
                    hid = se2.get("hostid")
                    if hid:
                        host_ids.add(str(hid))

        dbg(f"graph_ids={len(graph_ids)} trigger_ids={len(trigger_ids)} host_ids={len(host_ids)} group_ids={len(group_ids)}")

        if self.INCLUDE_TRIGGERS and (host_ids or group_ids):
            p = {"jsonrpc": "2.0", "method": "trigger.get", "params": {"output": "extend", "expandExpression": 1, "selectItems": "extend"}, "auth": auth, "id": 18}
            if host_ids and self.INCLUDE_HOSTS:
                p["params"]["hostids"] = list(host_ids)
            if group_ids and self.INCLUDE_HOSTS:
                p["params"]["groupids"] = list(group_ids)
            r = self._post(p)
            for t in r.get("result", []) or []:
                trigger_ids.add(str(t["triggerid"]))
            dbg(f"trigger ids after scan={len(trigger_ids)}")

        graphs, items_from_graphs, hosts_from_graphs = self.get_graphs_items_hosts(graph_ids, auth)
        triggers, items_from_triggers, hosts_from_triggers = self.get_triggers_items_hosts(trigger_ids, auth, {str(i["itemid"]): i for i in items_from_graphs}, {str(h["hostid"]): h for h in hosts_from_graphs})

        all_items = items_from_graphs + items_from_triggers
        all_hosts = hosts_from_graphs + hosts_from_triggers
        dbg(f"all_items={len(all_items)} all_hosts={len(all_hosts)}")

        host_groups_map, unique_host_groups = self.get_host_groups_for_hosts([h["hostid"] for h in all_hosts], auth)
        for h in all_hosts:
            h["groups"] = host_groups_map.get(h["hostid"], [])
        dbg(f"host_groups total={sum(len(v) for v in host_groups_map.values())} unique_groups={len(unique_host_groups)}")

        tpl_ids = set()
        for it in all_items:
            tid = it.get("templateid")
            if tid:
                tpl_ids.add(str(tid))
        for tr in triggers:
            tid = tr.get("templateid")
            if tid:
                tpl_ids.add(str(tid))
        dbg(f"template candidate ids initial={len(tpl_ids)}")

        host_ids_list = [h["hostid"] for h in all_hosts]
        if self.INCLUDE_TEMPLATES and host_ids_list:
            p_ht = {"jsonrpc": "2.0", "method": "host.get", "params": {"hostids": host_ids_list, "output": [], "selectParentTemplates": ["templateid"]}, "auth": auth, "id": 30}
            r_ht = self._post(p_ht)
            for h in r_ht.get("result", []) or []:
                for tpl in h.get("parentTemplates", []) or []:
                    tpl_ids.add(str(tpl["templateid"]))
            dbg(f"template ids after parentTemplates={len(tpl_ids)}")

        templates, template_groups = self.get_templates_and_groups(list(tpl_ids), auth)

        additional = set()
        for tpl in templates:
            for pt in tpl.get("parentTemplates", []) or []:
                additional.add(str(pt["templateid"]))
        if additional:
            new_ids = list(additional - tpl_ids)
            if new_ids:
                more_templates, more_groups = self.get_templates_and_groups(new_ids, auth)
                templates.extend(more_templates)
                for g in more_groups:
                    if g not in template_groups:
                        template_groups.append(g)
        dbg(f"final templates={len(templates)} template_groups={len(template_groups)}")

        plain_items = [it for it in all_items if not it.get("templateid")]
        dbg(f"plain_items (non-template) count={len(plain_items)}")

        items_map = {}
        for it in all_items + [itm for tpl in templates for itm in tpl.get("items", [])]:
            if it.get("itemid"):
                items_map[str(it["itemid"])] = {"host": it.get("host", "UNKNOWN_HOST"), "key_": it.get("key_", "UNKNOWN_KEY")}
        dbg(f"items_map entries={len(items_map)}")

        if self.INCLUDE_TRIGGERS:
            changed = 0
            for tr in triggers:
                expr = tr.get("expression") or ""
                mapped = self.map_trigger_expression(expr, items_map)
                if mapped and mapped != expr:
                    tr["expression_mapped"] = mapped
                    changed += 1
                rec_expr = tr.get("recovery_expression") or ""
                mapped_rec = self.map_trigger_expression(rec_expr, items_map) if rec_expr else ""
                if mapped_rec and mapped_rec != rec_expr:
                    tr["recovery_expression_mapped"] = mapped_rec
            dbg(f"trigger expressions mapped={changed}")

        export_triggers = []
        for tr in triggers:
            t = dict(tr)
            t["expression_original"] = tr.get("expression")
            t["recovery_expression_original"] = tr.get("recovery_expression")
            t["expression"] = tr.get("expression_mapped", tr.get("expression"))
            t["recovery_expression"] = tr.get("recovery_expression_mapped", tr.get("recovery_expression"))
            export_triggers.append(t)

        if not self.INCLUDE_TRIGGERS:
            export_triggers = []

        export_data = {
            "export_info": {"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "dashboard_id": dashboard_id, "exported_by": self.USERNAME},
            "dashboard": dashboard,
            "widgets": widgets,
            "maps": maps,
            "graphs": graphs if self.INCLUDE_GRAPHS else [],
            "hosts": all_hosts if self.INCLUDE_HOSTS else [],
            "host_groups": unique_host_groups if self.INCLUDE_HOSTS else [],
            "items": plain_items,
            "triggers": export_triggers,
            "templates": templates if self.INCLUDE_TEMPLATES else [],
            "template_groups": template_groups if self.INCLUDE_TEMPLATES else [],
        }

        with open(export_file, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=self.PRETTY_JSON_INDENT, ensure_ascii=False)
        dbg(f"wrote export_data file={export_file} size={os.path.getsize(export_file)}")

        cfg_format = self.EXPORT_FORMAT
        cfg_hosts = [h["hostid"] for h in all_hosts] if self.INCLUDE_HOSTS else []
        cfg_templates = [t["templateid"] for t in templates] if self.INCLUDE_TEMPLATES else []
        cfg_maps = [m.get("sysmapid") or m.get("mapid") or m.get("id") for m in maps]
        dbg(f"configuration.export inputs hosts={len(cfg_hosts)} templates={len(cfg_templates)} maps={len(cfg_maps)} format={cfg_format}")
        cfg_str = self.configuration_export(auth, hosts=cfg_hosts, templates=cfg_templates, maps=cfg_maps, format_=cfg_format)
        cfg_suffix = "yml" if cfg_format == "yaml" else cfg_format
        cfg_file = os.path.join(self.EXPORT_DIR, f"{self.FILE_PREFIX}{dashboard_id}.config.{cfg_suffix}")
        with open(cfg_file, "w", encoding="utf-8") as f:
            f.write(cfg_str)
        dbg(f"wrote config_export file={cfg_file} size={os.path.getsize(cfg_file)}")

    def run(self):
        """Main execution method."""
        os.makedirs(self.EXPORT_DIR, exist_ok=True)
        auth = self.login_to_zabbix()
        try:
            dashboard_id = None
            if self.DASHBOARD_ID_CFG:
                dashboard_id = str(self.DASHBOARD_ID_CFG)
            elif self.DASHBOARD_NAME:
                dashboard_id = self.get_dashboard_id_by_name(auth, self.DASHBOARD_NAME)
                if not dashboard_id:
                    print(f"Dashboard mit Name '{self.DASHBOARD_NAME}' nicht gefunden.")
                    return False
            else:
                print("Kein 'dashboard_id' oder 'dashboard_name' gesetzt.")
                return False
            dbg(f"effective dashboard_id={dashboard_id}")
            self.export_dashboard(dashboard_id, auth)
            return True
        finally:
            self.logout_from_zabbix(auth)


def main():
    parser = argparse.ArgumentParser(description="Export Zabbix Dashboard")
    parser.add_argument('--url', help='Zabbix API URL')
    parser.add_argument('--username', help='Zabbix username')
    parser.add_argument('--password', help='Zabbix password')
    parser.add_argument('--ca-cert', default='', help='CA certificate path')
    parser.add_argument('--dashboard-name', help='Dashboard name to export')
    parser.add_argument('--dashboard-id', help='Dashboard ID to export')
    parser.add_argument('--export-dir', default='exports', help='Export directory')
    parser.add_argument('--export-format', default='json', choices=['json', 'yaml'], help='Export format')
    
    args = parser.parse_args()
    
    # Check if running with command-line arguments
    if args.url and args.username and args.password:
        # Use command-line arguments
        exporter = ZabbixDashboardExporter(
            url=args.url,
            username=args.username,
            password=args.password,
            ca_certificate_path=args.ca_cert,
            dashboard_name=args.dashboard_name,
            dashboard_id=args.dashboard_id,
            export_dir=args.export_dir,
            export_format=args.export_format
        )
        success = exporter.run()
        sys.exit(0 if success else 1)
    
    # Fall back to config files
    if not CONFIGS_AVAILABLE:
        print("Error: Config modules not available and no command-line arguments provided")
        parser.print_help()
        sys.exit(1)
    
    dashboard_config = load_config("dashboard_config.json")
    source_config = load_source_config("config.json")
    
    exporter = ZabbixDashboardExporter(
        url=source_config["source_url"],
        username=source_config["source_username"],
        password=source_config["source_password"],
        ca_certificate_path=source_config.get("source_ca_certificate_path", ''),
        export_format=dashboard_config.get("export_format", "json"),
        export_dir=dashboard_config.get("export_dir", "exports"),
        dashboard_name=dashboard_config.get("dashboard_name"),
        dashboard_id=dashboard_config.get("dashboard_id"),
        file_prefix=dashboard_config.get("file_prefix", "dashboard_"),
        pretty_json_indent=dashboard_config.get("pretty_json_indent", 2),
        max_threads=dashboard_config.get("max_threads", 5),
        request_timeout=dashboard_config.get("request_timeout", 30),
        map_batch_size=dashboard_config.get("map_batch_size", 10),
        include_submaps=dashboard_config.get("include_submaps", True),
        include_upper_maps=dashboard_config.get("include_upper_maps", True),
        include_templates=dashboard_config.get("include_templates", True),
        include_triggers=dashboard_config.get("include_triggers", True),
        include_graphs=dashboard_config.get("include_graphs", True),
        include_hosts=dashboard_config.get("include_hosts", True),
        regex_options=dashboard_config.get("regex_options", {}),
        process_aliases=dashboard_config.get("process_aliases", {}),
        process_to_key=dashboard_config.get("process_to_key", {}),
        cache_to_key=dashboard_config.get("cache_to_key", {})
    )
    
    success = exporter.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
