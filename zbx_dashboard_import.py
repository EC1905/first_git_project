import json
import os
import base64
import requests
from typing import Dict, List, Set, Tuple, Any, Optional

from config import load_config

main_config = load_config("config.json")
dashboard_config = load_config("dashboard_config.json")

ZABBIX_URL          = main_config["target_url"]
USERNAME            = main_config["target_username"]
PASSWORD            = main_config["target_password"]
CA_CERTIFICATE_PATH = main_config["target_ca_certificate_path"]
SESSION_TIMEOUT     = dashboard_config["request_timeout"]
EXPORT_DIR          = dashboard_config["export_dir"]
CONFIG_FILE_PATH    = dashboard_config["config_file_path"]
DASHBOARD_FILE_PATH = dashboard_config["dashboard_file_path"]
TRIGGER_NAME_KEYS   = dashboard_config["trigger_name_keys"]

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})
session.verify = CA_CERTIFICATE_PATH

def dbg(msg: str):
    print(f"[DEBUG] {msg}")

def info(msg: str):
    print(f"[INFO] {msg}")

def zbx_call(payloads):
    if isinstance(payloads, dict):
        payloads = [payloads]
    dbg(f"API CALL {payloads[0].get('method')} count={len(payloads)}")
    resp = session.post(ZABBIX_URL, json=payloads, timeout=SESSION_TIMEOUT)
    try:
        data = resp.json()
    except Exception:
        dbg(f"HTTP {resp.status_code} text={resp.text[:500]}")
        resp.raise_for_status()
        data = []
    if isinstance(data, dict):
        data = [data]
    results = []
    for entry in data:
        if "error" in entry:
            dbg(f"API ERROR method={payloads[0].get('method')} error={entry['error']}")
            raise RuntimeError(f"Zabbix API error: {entry['error']}")
        results.append(entry.get("result"))
    return results

def login():
    payload = {"jsonrpc":"2.0","method":"user.login","params":{"username":USERNAME,"password":PASSWORD},"auth":None,"id":1}
    token = zbx_call(payload)[0]
    dbg(f"login token_present={bool(token)}")
    return token

def logout(token):
    payload = {"jsonrpc":"2.0","method":"user.logout","params":[],"auth":token,"id":2}
    try:
        zbx_call(payload)[0]
        dbg("logout ok")
    except Exception as e:
        dbg(f"logout failed: {e}")

def get_existing_index(token, method, key, params=None):
    params = params or {}
    payload = {"jsonrpc":"2.0","method":method,"params":params,"auth":token,"id":30}
    res = zbx_call(payload)[0] or []
    idx = {obj.get(key): obj for obj in res if key in obj}
    dbg(f"index {method} size={len(idx)} key={key}")
    return idx

def create_obj(token, method, params):
    payload = {"jsonrpc":"2.0","method":method,"params":params,"auth":token,"id":101}
    res = zbx_call(payload)[0]
    dbg(f"create {method} ok keys={list(res.keys()) if isinstance(res, dict) else type(res)}")
    return res

def update_obj(token, method, params):
    payload = {"jsonrpc":"2.0","method":method,"params":params,"auth":token,"id":105}
    res = zbx_call(payload)[0]
    dbg(f"update {method} ok")
    return res

DEFAULT_ICON_NAME = "Default Node Icon"
DEFAULT_ICON_PNG_1x1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAA"
    "AAC0lEQVR42mP8/x8AAwMB/ajqP7EAAAAASUVORK5CYII="
)

def ensure_default_icon_image(token) -> str:
    existing = get_existing_index(token, "image.get", "name",
                                  {"output": ["imageid","name"], "filter": {"name": [DEFAULT_ICON_NAME]}})
    if DEFAULT_ICON_NAME in existing:
        return existing[DEFAULT_ICON_NAME]["imageid"]
    try:
        res = create_obj(token, "image.create",
                         {"name": DEFAULT_ICON_NAME, "imagetype": 1, "image": DEFAULT_ICON_PNG_1x1})
        return res["imageids"][0]
    except RuntimeError:
        imgs = zbx_call({"jsonrpc":"2.0","method":"image.get",
                         "params":{"output":["imageid","name"]},"auth":token,"id":901})[0] or []
        if imgs:
            dbg(f"[FALLBACK] using existing image '{imgs[0].get('name')}' id={imgs[0].get('imageid')}")
            return imgs[0]["imageid"]
        dbg("[FALLBACK] no images available; using iconid_off=0")
        return "0"


_cached_export_source_data: Optional[Dict[str, Any]] = None


def export_root(data):
    return data.get("zabbix_export", data)


def export_maps(data) -> List[Dict[str, Any]]:
    return export_root(data).get("maps", []) or []


def export_map_index_by_id_and_name(data):
    by_id = {}
    by_name = {}
    for m in export_maps(data):
        nm = (m.get("name") or "").strip()
        mid = m.get("sysmapid") or m.get("mapid") or m.get("id")
        if nm:
            by_name[nm] = m
        if mid is not None:
            by_id[str(mid).strip()] = m
            if str(mid).strip().isdigit():
                by_id[str(int(mid))] = m
    dbg(f"[REMAP-A/INDEX] export maps indexed by_id={len(by_id)} by_name={len(by_name)}")
    return by_id, by_name


def export_get_map_name_by_id(data, old_id) -> Optional[str]:
    if old_id is None:
        return None
    by_id, _ = export_map_index_by_id_and_name(data)
    candidates = []
    if isinstance(old_id, (int, float)):
        candidates = [str(int(old_id))]
    elif isinstance(old_id, str):
        s = old_id.strip()
        candidates = [s]
        if s.isdigit():
            candidates.append(str(int(s)))
    for c in candidates:
        if c in by_id:
            nm = by_id[c].get("name")
            return nm.strip() if isinstance(nm, str) else nm
    return None


def build_merged_lookup_export(primary: Dict[str, Any], secondary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged_root: Dict[str, Any] = {"maps": [], "hosts": []}
    for src in (primary, secondary):
        if not src:
            continue
        r = export_root(src)
        if isinstance(r, dict):
            m = r.get("maps")
            if isinstance(m, list):
                merged_root["maps"].extend(m)
            h = r.get("hosts")
            if isinstance(h, list):
                merged_root["hosts"].extend(h)
    return {"zabbix_export": merged_root}


def _map_name_index(token) -> Dict[str, Dict[str, Any]]:
    return get_existing_index(token, "map.get", "name", {"output":["sysmapid","name"]})


def _group_name_index(token) -> Dict[str, Dict[str, Any]]:
    return get_existing_index(token, "hostgroup.get", "name", {"output":["groupid","name"]})


def _host_name_index(token) -> Dict[str, Dict[str, Any]]:
    return get_existing_index(token, "host.get", "host", {"output":["hostid","host"]})


def _norm(s: Optional[str]) -> Optional[str]:
    return s.strip() if isinstance(s, str) else s


def _build_host_selement(se, hosts_idx, source_id_to_name, default_icon_id):
    elems = se.get("elements", []) or []
    cand_names = []
    cand_ids = []
    if elems:
        e0 = elems[0]
        cand_ids.append(e0.get("hostid") or e0.get("host_id"))
        cand_names += [e0.get("host"), e0.get("host_name"), e0.get("name")]
    cand_names += [se.get("host"), se.get("label")]
    cand_names = [_norm(x) for x in cand_names if _norm(x)]
    hostid = None
    for cid in [x for x in cand_ids if x]:
        sid = str(cid)
        if sid in source_id_to_name:
            nm = source_id_to_name[sid]
            if nm in hosts_idx:
                hostid = hosts_idx[nm]["hostid"]
                break
    if not hostid:
        for nm in cand_names:
            if nm in hosts_idx:
                hostid = hosts_idx[nm]["hostid"]
                break
    if hostid:
        return {
            "elementtype": 0,
            "x": int(se.get("x", 0)),
            "y": int(se.get("y", 0)),
            "label": se.get("label"),
            "label_location": int(se.get("label_location", 0)),
            "iconid_off": se.get("iconid_off") or default_icon_id,
            "width": int(se.get("width", se.get("w", 100))) if str(se.get("width","" )).isdigit() else se.get("width"),
            "height": int(se.get("height", se.get("h", 100))) if str(se.get("height","" )).isdigit() else se.get("height"),
            "elements": [{"hostid": str(hostid)}]
        }
    dbg(f"[OFFENDER] HOST unresolved -> label='{se.get('label')}' candidates={cand_names}")
    return None


def _build_group_selement(se, groups_idx, default_icon_id):
    elems = se.get("elements", []) or []
    cand_names = []
    if elems:
        e0 = elems[0]
        cand_names += [e0.get("name"), e0.get("group"), e0.get("group_name")]
    cand_names += [se.get("label")]
    cand_names = [_norm(x) for x in cand_names if _norm(x)]
    gid = None
    for nm in cand_names:
        if nm in groups_idx:
            gid = groups_idx[nm]["groupid"]
            break
    if gid:
        return {
            "elementtype": 1,
            "x": int(se.get("x", 0)),
            "y": int(se.get("y", 0)),
            "label": se.get("label"),
            "label_location": int(se.get("label_location", 0)),
            "iconid_off": se.get("iconid_off") or default_icon_id,
            "width": int(se.get("width", se.get("w", 100))) if str(se.get("width","" )).isdigit() else se.get("width"),
            "height": int(se.get("height", se.get("h", 100))) if str(se.get("height","" )).isdigit() else se.get("height"),
            "elements": [{"groupid": str(gid)}]
        }
    dbg(f"[OFFENDER] GROUP unresolved -> label='{se.get('label')}' candidates={cand_names}")
    return None


def _build_submap_selement(se, target_name_to_id: Dict[str, Any], default_icon_id):
    elems = se.get("elements", []) or []
    cand_names: List[str] = []
    cand_ids: List[Any] = []
    if elems:
        e0 = elems[0]
        cand_ids.append(e0.get("sysmapid") or e0.get("mapid"))
        cand_names += [e0.get("name"), e0.get("mapname")]
    cand_names += [se.get("label")]
    cand_names = [_norm(x) for x in cand_names if _norm(x)]

    target_ids_as_str = {str(v) for v in target_name_to_id.values() if v is not None}

    # Prefer direct name resolution
    for nm in cand_names:
        if nm in target_name_to_id:
            return {
                "elementtype": 3,
                "x": int(se.get("x", 0)),
                "y": int(se.get("y", 0)),
                "label": se.get("label"),
                "label_location": int(se.get("label_location", 0)),
                "iconid_off": se.get("iconid_off") or default_icon_id,
                "width": int(se.get("width", se.get("w", 100))) if str(se.get("width","" )).isdigit() else se.get("width"),
                "height": int(se.get("height", se.get("h", 100))) if str(se.get("height","" )).isdigit() else se.get("height"),
                "elements": [{"sysmapid": str(target_name_to_id[nm])}]
            }

    # If source provided an id, map it to source name then to target id
    for cid in [x for x in cand_ids if x is not None]:
        sid = str(int(cid)) if isinstance(cid, (int, float)) or (isinstance(cid, str) and cid.isdigit()) else str(cid).strip()

        # If the id already matches a target id (rare, but possible), accept it directly
        if sid in target_ids_as_str:
            return {
                "elementtype": 3,
                "x": int(se.get("x", 0)),
                "y": int(se.get("y", 0)),
                "label": se.get("label"),
                "label_location": int(se.get("label_location", 0)),
                "iconid_off": se.get("iconid_off") or default_icon_id,
                "width": int(se.get("width", se.get("w", 100))) if str(se.get("width","" )).isdigit() else se.get("width"),
                "height": int(se.get("height", se.get("h", 100))) if str(se.get("height","" )).isdigit() else se.get("height"),
                "elements": [{"sysmapid": sid}]
            }

        nm_exp = export_get_map_name_by_id(_cached_export_source_data or {}, sid)
        if nm_exp and nm_exp in target_name_to_id:
            return {
                "elementtype": 3,
                "x": int(se.get("x", 0)),
                "y": int(se.get("y", 0)),
                "label": se.get("label"),
                "label_location": int(se.get("label_location", 0)),
                "iconid_off": se.get("iconid_off") or default_icon_id,
                "width": int(se.get("width", se.get("w", 100))) if str(se.get("width","" )).isdigit() else se.get("width"),
                "height": int(se.get("height", se.get("h", 100))) if str(se.get("height","" )).isdigit() else se.get("height"),
                "elements": [{"sysmapid": str(target_name_to_id[nm_exp])}]
            }

    dbg(f"[OFFENDER] SUBMAP unresolved -> labels={cand_names} ids={cand_ids}")
    return None


def build_source_hostid_to_name(export_like_obj: Dict[str, Any]) -> Dict[str, str]:
    root = export_root(export_like_obj)
    mapping: Dict[str, str] = {}
    for h in root.get("hosts", []) or []:
        sid = h.get("hostid") or h.get("id")
        name = h.get("host") or h.get("name")
        if sid and name:
            mapping[str(sid)] = name
    return mapping


def import_maps_from_any_export(token, export_like_obj: Dict[str, Any], image_ids: Dict[str,str], hosts_idx_initial):
    root = export_root(export_like_obj)
    maps = root.get("maps", [])
    default_icon_id = ensure_default_icon_image(token)
    source_id_to_name = build_source_hostid_to_name(export_like_obj)

    # First, ensure target maps exist and collect name->id mapping
    name_to_id: Dict[str, str] = {}
    for m in maps or []:
        name = m.get("name")
        if not name:
            continue
        existing = get_existing_index(token, "map.get", "name", {"output": ["sysmapid","name"], "filter": {"name": [name]}})
        if name in existing:
            name_to_id[name] = existing[name]["sysmapid"]
            continue
        res = create_obj(token, "map.create", {"name": name, "width": int(m.get("width", 800)), "height": int(m.get("height", 600))})
        name_to_id[name] = res["sysmapids"][0]
        info(f"Map angelegt: {name} -> {name_to_id[name]}")

    # Build fresh indices and prefer provided hosts index if available
    maps_idx_by_name = _map_name_index(token)
    groups_idx = _group_name_index(token)
    hosts_idx  = hosts_idx_initial or _host_name_index(token)

    built_by_map: Dict[str, List[Dict[str, Any]]] = {}
    offenders: Dict[str, List[Dict[str, Any]]] = {}

    for m in maps or []:
        name = m.get("name")
        selements: List[Dict[str, Any]] = []
        bad: List[Dict[str, Any]] = []
        for se in m.get("selements", []) or []:
            et = int(se.get("elementtype", se.get("selementtype", 0)))
            built = None
            if et == 0:
                built = _build_host_selement(se, hosts_idx, source_id_to_name, default_icon_id)
            elif et == 1:
                built = _build_group_selement(se, groups_idx, default_icon_id)
            elif et == 3:
                built = _build_submap_selement(se, name_to_id, default_icon_id)
            else:
                built = {
                    "elementtype": 4,
                    "x": int(se.get("x", 0)),
                    "y": int(se.get("y", 0)),
                    "label": se.get("label"),
                    "label_location": int(se.get("label_location", 0)),
                    "iconid_off": se.get("iconid_off") or default_icon_id,
                    "width": int(se.get("width", se.get("w", 100))) if str(se.get("width","" )).isdigit() else se.get("width"),
                    "height": int(se.get("height", se.get("h", 100))) if str(se.get("height","" )).isdigit() else se.get("height")
                }
            if built:
                selements.append(built)
            else:
                bad.append(se)
        built_by_map[name] = selements
        offenders[name] = bad

    # Second pass: attempt to resolve remaining offenders (after targets exist)
    for m in maps or []:
        name = m.get("name")
        for se in offenders.get(name, []):
            et = int(se.get("elementtype", se.get("selementtype", 0)))
            if "label" in se and isinstance(se["label"], str):
                se["label"] = se["label"].strip()
            built = None
            if et == 0:
                built = _build_host_selement(se, hosts_idx, source_id_to_name, default_icon_id)
            elif et == 1:
                built = _build_group_selement(se, groups_idx, default_icon_id)
            elif et == 3:
                built = _build_submap_selement(se, name_to_id, default_icon_id)
            if built:
                built_by_map[name].append(built)
            else:
                fallback = {
                    "elementtype": 4,
                    "x": int(se.get("x", 0)),
                    "y": int(se.get("y", 0)),
                    "label": se.get("label"),
                    "label_location": int(se.get("label_location", 0)),
                    "iconid_off": se.get("iconid_off") or default_icon_id,
                    "width": int(se.get("width", se.get("w", 100))) if str(se.get("width","" )).isdigit() else se.get("width"),
                    "height": int(se.get("height", se.get("h", 100))) if str(se.get("height","" )).isdigit() else se.get("height")
                }
                dbg(f"[FALLBACK] map='{name}' selement label='{se.get('label')}' -> IMAGE")
                built_by_map[name].append(fallback)

    # Build links by matching positional keys
    for m in maps or []:
        name = m.get("name")
        sysmapid = name_to_id.get(name)
        if not sysmapid:
            continue

        def mk_key(se):
            et = int(se.get("elementtype", se.get("selementtype", 0)))
            return f"{et}:{se.get('x')}:{se.get('y')}:{(se.get('label') or '')}"

        source_keys = [mk_key(se) for se in m.get("selements", []) or []]
        target_index: Dict[str, int] = {}
        for idx, selem in enumerate(built_by_map[name]):
            et = int(selem.get("elementtype", 4))
            key = f"{et}:{selem.get('x')}:{selem.get('y')}:{(selem.get('label') or '')}"
            if idx < len(source_keys):
                target_index[source_keys[idx]] = idx

        uid_to_index: Dict[str, int] = {}
        for se_src in m.get("selements", []) or []:
            uid = se_src.get("selement_uid")
            if not uid:
                continue
            key = mk_key(se_src)
            if key in target_index:
                uid_to_index[uid] = target_index[key]

        valid_links = []
        for ln in m.get("links", []) or []:
            t1 = None; t2 = None
            ep1 = ln.get("endpoint1") or {}; ep2 = ln.get("endpoint2") or {}
            u1 = ep1.get("selement_uid"); u2 = ep2.get("selement_uid")
            if u1 in uid_to_index: t1 = uid_to_index[u1]
            if u2 in uid_to_index: t2 = uid_to_index[u2]
            if t1 is not None and t2 is not None:
                valid_links.append({
                    "selementid1": t1, "selementid2": t2,
                    "drawtype": int(ln.get("drawtype", 0)),
                    "color": ln.get("color") or ln.get("linecolor") or "000000",
                    "width": int(ln.get("width", 1))
                })

        info(f"map.update '{name}' selements={len(built_by_map[name])} links={len(valid_links)}")
        try:
            update_obj(token, "map.update", {"sysmapid": sysmapid, "selements": built_by_map[name], "links": valid_links})
        except RuntimeError as e:
            print(f"Map '{name}' konnte nicht aktualisiert werden: {e}")

    return name_to_id


def _is_sysmapid_field_name(n: str) -> bool:
    if n == "sysmapid":
        return True
    if n.endswith(".sysmapid"):
        return True
    if n.startswith("sysmapid."):
        return True
    return False


def _remap_all_sysmapid_fields_of_widget(widget: Dict[str, Any], maps_idx: Dict[str, Any], page_index: int, widget_index: int):
    if not isinstance(widget.get("fields"), list):
        return
    new_fields = []
    for f in widget["fields"]:
        if not isinstance(f, dict):
            new_fields.append(f); continue
        nm = f.get("name")
        if not isinstance(nm, str):
            new_fields.append(f); continue
        if not _is_sysmapid_field_name(nm):
            new_fields.append(f); continue
        old_val = f.get("value")
        dbg(f"[REMAP-A0] p#{page_index} w#{widget_index} field '{nm}' value={old_val}")
        map_name = export_get_map_name_by_id(_cached_export_source_data or {}, old_val)
        dbg(f"[REMAP-A] p#{page_index} w#{widget_index} field '{nm}' name from export by id={old_val} -> '{map_name}'")
        if not map_name and ".sysmapid" in nm:
            prefix = nm.rsplit(".sysmapid", 1)[0]
            alt_name_key = f"{prefix}.name"
            for fx in widget["fields"]:
                if fx.get("name") == alt_name_key and isinstance(fx.get("value"), str) and fx.get("value").strip():
                    map_name = fx.get("value").strip()
                    dbg(f"[REMAP-A-FB] p#{page_index} w#{widget_index} field '{alt_name_key}' -> '{map_name}'")
                    break
        exists = map_name in maps_idx if map_name else False
        new_id = int(maps_idx[map_name]["sysmapid"]) if exists else None
        dbg(f"[REMAP-B] p#{page_index} w#{widget_index} field '{nm}' target has '{map_name}': {exists}")
        dbg(f"[REMAP-C] p#{page_index} w#{widget_index} field '{nm}' target sysmapid for '{map_name}': {new_id}")
        if new_id is not None:
            nf = dict(f); nf["value"] = new_id; new_fields.append(nf)
            dbg(f"[REMAP-D] p#{page_index} w#{widget_index} field '{nm}' set -> {new_id}")
        else:
            new_fields.append(f)
            dbg(f"[REMAP-D] p#{page_index} w#{widget_index} field '{nm}' unchanged")
    widget["fields"] = new_fields


def normalize_widget_sysmap_field(token, maps_idx, widget: Dict[str, Any], page_index:int=None, widget_index:int=None) -> None:
    _remap_all_sysmapid_fields_of_widget(widget, maps_idx, page_index, widget_index)


def sanitize_widgets_sysmapids(maps_idx, pages: List[Dict[str, Any]]) -> List[Tuple[int,int,Any]]:
    offenders = []
    valid_ids = {int(mi.get("sysmapid")) for mi in maps_idx.values() if mi.get("sysmapid")}
    for p_i, page in enumerate(pages or []):
        for w_i, w in enumerate(page.get("widgets", []) or []):
            fields = w.get("fields", [])
            clean_fields = []
            seen = set()
            for f in fields:
                nm = f.get("name")
                if nm in seen:
                    dbg(f"[SANITIZE] dedup field '{nm}' on widget p#{p_i} w#{w_i}"); continue
                seen.add(nm)
                if _is_sysmapid_field_name(nm):
                    val = f.get("value")
                    try: val_int = int(val)
                    except Exception: val_int = None
                    if val_int is None or val_int not in valid_ids:
                        offenders.append((p_i, w_i, val))
                        dbg(f"[SANITIZE] mark invalid sysmapid p#{p_i} w#{w_i} value={val} (keine Entfernung)")
                    clean_fields.append(f)
                else:
                    clean_fields.append(f)
            w["fields"] = clean_fields
    return offenders


def collect_trigger_names_from_maps_any(export_like_obj: Dict[str, Any]) -> Set[str]:
    names: Set[str] = set()
    def collect_from_maps(maps_list: List[Dict[str, Any]]):
        for m in maps_list or []:
            for ln in m.get("links", []) or []:
                for rule in ln.get("linktriggers", []) or []:
                    n = rule.get("trigger") or rule.get("description") or rule.get("name")
                    if n: names.add(str(n))
            for se in m.get("selements", []) or []:
                for k in TRIGGER_NAME_KEYS:
                    v = se.get(k)
                    if isinstance(v, str) and v.strip():
                        names.add(v.strip())
                for ru in se.get("elementTrigger", []) or []:
                    n = ru.get("trigger") or ru.get("name") or ru.get("description")
                    if n: names.add(str(n))
    root = export_root(export_like_obj)
    collect_from_maps(root.get("maps", []))
    return names


def preflight_verify_triggers_exist(token, trigger_names: Set[str]) -> Tuple[Set[str], Dict[str, str]]:
    missing: Set[str] = set()
    present: Dict[str, str] = {}
    if not trigger_names:
        return missing, present
    names = sorted(list(trigger_names))
    for i in range(0, len(names), 100):
        ch = names[i:i+100]
        res = zbx_call({"jsonrpc":"2.0","method":"trigger.get",
                        "params":{"output":["triggerid","description"], "filter":{"description": ch}},
                        "auth": token, "id": 701})[0] or []
        found = {r["description"]: r["triggerid"] for r in res if "description" in r}
        for n in ch:
            if n in found:
                present[n] = found[n]
            else:
                missing.add(n)
    return missing, present


def ensure_hostgroup(token, name):
    existing = get_existing_index(token, "hostgroup.get", "name",
                                  {"filter":{"name":[name]}, "output":["groupid","name"]})
    if name in existing:
        return existing[name]["groupid"]
    res = zbx_call({"jsonrpc":"2.0","method":"hostgroup.create",
                    "params":{"name":name},"auth":token,"id":141})[0]
    return res["groupids"][0]


def ensure_templategroup(token, name):
    try:
        existing = get_existing_index(token, "templategroup.get", "name",
                                      {"filter":{"name":[name]}, "output":["groupid","name"]})
        if name in existing:
            return existing[name]["groupid"]
        res = zbx_call({"jsonrpc":"2.0","method":"templategroup.create",
                        "params":{"name":name},"auth":token,"id":142})[0]
        return res["groupids"][0]
    except RuntimeError as e:
        if "Method not found" in str(e):
            return ensure_hostgroup(token, name)
        raise


def preload_required_groups_from_export_json(token, export_obj):
    created = []
    root = export_root(export_obj)
    hostgroup_names = set()
    templategroup_names = set()
    if isinstance(root, dict) and "groups" in root and isinstance(root["groups"], list):
        for g in root["groups"]:
            n = g.get("name")
            if n: hostgroup_names.add(n)
    for h in root.get("hosts", []):
        for g in h.get("groups", []):
            n = g.get("name") or (g.get("group", {}) or {}).get("name")
            if n: hostgroup_names.add(n)
    for t in root.get("templates", []):
        for g in t.get("groups", []):
            n = g.get("name") or (g.get("group", {}) or {}).get("name")
            if n: templategroup_names.add(n)
        for g in t.get("template_groups", []):
            n = g.get("name")
            if n: templategroup_names.add(n)
    if "Templates" in templategroup_names or "Templates" in hostgroup_names:
        templategroup_names.add("Templates"); hostgroup_names.add("Templates")
    for n in sorted(hostgroup_names):
        gid = ensure_hostgroup(token, n); created.append(("hostgroup", n, gid))
    for n in sorted(templategroup_names):
        gid = ensure_templategroup(token, n); created.append(("templategroup", n, gid))
    return created


def assign_templates_to_hosts_from_any_export(token, export_like_obj: Dict[str, Any]):
    root = export_root(export_like_obj)
    tpl_names = set()
    for t in root.get("templates", []) or []:
        if "template" in t and isinstance(t["template"], dict):
            n = t["template"].get("name") or t["template"].get("host")
        else:
            n = t.get("host") or t.get("name")
        if n: tpl_names.add(n)
    tpl_index = get_existing_index(token, "template.get", "host",
                                   {"filter":{"host": list(tpl_names)}, "output":["templateid","host"]})
    updated = 0
    for h in root.get("hosts", []) or []:
        host_name = h.get("host") or h.get("name")
        if not host_name: continue
        host_idx = get_existing_index(token, "host.get", "host",
                                      {"filter":{"host":[host_name]}, "output":["hostid","host"],
                                       "selectParentTemplates":["templateid","host"]})
        if host_name not in host_idx: continue
        hid = host_idx[host_name]["hostid"]
        already = {t["templateid"] for t in host_idx[host_name].get("parentTemplates", [])}
        tpl_list = []
        for t in h.get("templates", []) or []:
            tname = t.get("name") or t.get("host")
            if tname and tname in tpl_index:
                tid = tpl_index[tname]["templateid"]
                if tid not in already:
                    tpl_list.append({"templateid": tid})
        if tpl_list:
            update_obj(token, "host.update", {"hostid": hid, "templates": tpl_list}); updated += 1
    if updated:
        print(f"Hosts aktualisiert (Templates zugewiesen): {updated}")


def recreate_dashboard_from_file(token, dashboard_file_path, extra_export_for_lookup: Optional[Dict[str, Any]] = None):
    with open(dashboard_file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    global _cached_export_source_data
    # Merge the dashboard export with an optional configuration export for robust id->name resolution
    _cached_export_source_data = build_merged_lookup_export(data, extra_export_for_lookup)

    trig_names = collect_trigger_names_from_maps_any(data)
    missing, present = preflight_verify_triggers_exist(token, trig_names)
    if missing:
        print("Preflight: Fehlende Triggernamen:")
        for n in sorted(missing): print(f" - {n}")

    images = export_root(data).get("images", [])
    image_ids = import_images_from_zbx_export(token, images)

    hosts_idx_for_maps = _host_name_index(token)
    import_maps_from_any_export(token, data, image_ids, hosts_idx_for_maps)

    dash = None
    zbx_export = data.get("zabbix_export")
    if isinstance(zbx_export, dict):
        d = zbx_export.get("dashboards")
        if isinstance(d, list) and d: dash = d[0]
        elif isinstance(d, dict): dash = d
    if not dash: dash = data.get("dashboard")
    if not dash:
        print("Kein Dashboard im Export gefunden, überspringe Dashboard-Anlage.")
        return

    candidate_name = dash.get("name") or "Imported Dashboard"
    existing = get_existing_index(token, "dashboard.get", "name",
                                  {"filter":{"name":[candidate_name]},"output":["dashboardid","name"]})
    if candidate_name in existing:
        print(f"Dashboard existiert bereits, wird übersprungen: {candidate_name}")
        return

    hosts_idx  = _host_name_index(token)
    groups_idx = _group_name_index(token)
    maps_idx   = _map_name_index(token)
    graphs_idx = get_existing_index(token, "graph.get", "name", {"output":["graphid","name"]})
    trig_idx   = get_existing_index(token, "trigger.get", "description", {"output":["triggerid","description"]})

    clean = {
        "name": candidate_name,
        "private": int(dash.get("private", 1)),
        "display_period": int(dash.get("displayperiod", dash.get("display_period", 30))),
        "auto_start": int(dash.get("autostart", dash.get("auto_start", 1))),
        "users": dash.get("users", []),
        "userGroups": dash.get("userGroups", []),
        "pages": []
    }
    for p_i, page in enumerate(dash.get("pages", [])):
        new_widgets = []
        for w_i, w in enumerate(page.get("widgets", [])):
            nw = {k: v for k, v in w.items() if k not in ["widgetid","dashboardid","dashboard_pageid","dashboard_page","dashboard_pageid"]}
            fields = []
            for f in w.get("fields", []):
                f = dict(f); nm = f.get("name")
                if nm == "hostids":
                    v = f.get("value", [])
                    if isinstance(v, list): f["value"] = [hosts_idx[v]["hostid"] for v in v if v in hosts_idx]
                elif nm == "groupids":
                    v = f.get("value", [])
                    if isinstance(v, list): f["value"] = [groups_idx[v]["groupid"] for v in v if v in groups_idx]
                elif _is_sysmapid_field_name(nm):
                    pass
                elif nm == "graphid":
                    v = f.get("value")
                    if isinstance(v, str) and v in graphs_idx: f["value"] = graphs_idx[v]["graphid"]
                elif nm == "triggerid":
                    v = f.get("value")
                    if isinstance(v, str) and v in trig_idx: f["value"] = trig_idx[v]["triggerid"]
                if "value" in f and f["value"] not in (None, "", []): fields.append(f)
            nw["fields"] = fields
            normalize_widget_sysmap_field(token, maps_idx, nw, page_index=p_i, widget_index=w_i)
            new_widgets.append(nw)
        clean["pages"].append({"name": page.get("name","Page"), "widgets": new_widgets})

    offenders = sanitize_widgets_sysmapids(maps_idx, clean["pages"])
    if offenders:
        for p_i, w_i, val in offenders:
            dbg(f"[OFFENDER] Widget p#{p_i} w#{w_i} invalid sysmapid im Ergebnis value={val} (nur markiert)")

    create_obj(token, "dashboard.create", clean)
    print(f"Dashboard erstellt: {clean['name']}")


def import_images_from_zbx_export(token, images):
    image_ids = {}
    for img in images or []:
        name = img.get("name")
        if not name: continue
        existing = get_existing_index(token, "image.get", "name", {"output": ["imageid","name"], "filter": {"name": [name]}})
        if name in existing:
            image_ids[name] = existing[name]["imageid"]; continue
        payload = {"name": name, "imagetype": int(img.get("imagetype", 1)), "image": img.get("encodedImage")}
        try:
            res = create_obj(token, "image.create", payload)
            image_ids[name] = res["imageids"][0]
            print(f"Bild angelegt: {name} -> {image_ids[name]}")
        except RuntimeError as e:
            print(f"Bild '{name}' konnte nicht angelegt werden: {e}")
    return image_ids


def configuration_import_without_maps(token, source_str, format_="json"):
    rules = {
        "hosts": {"createMissing": True, "updateExisting": True},
        "templates": {"createMissing": True, "updateExisting": True},
        "items": {"createMissing": True, "updateExisting": True},
        "triggers": {"createMissing": True, "updateExisting": True},
        "graphs": {"createMissing": True, "updateExisting": True},
        "valueMaps": {"createMissing": True, "updateExisting": True}
    }
    payload = {"jsonrpc": "2.0","method": "configuration.import","params": {"format": format_, "rules": rules, "source": source_str},"auth": token,"id": 151}
    return zbx_call(payload)[0]


def main():
    token = login()
    try:
        cfg_path = CONFIG_FILE_PATH
        if not cfg_path or not os.path.exists(cfg_path):
            print("Konfigurationsdatei (configuration.export) nicht gefunden oder nicht gesetzt: config_file_path")
            return

        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()

        fmt = "json"
        lp = cfg_path.lower()
        if lp.endswith((".yml", ".yaml")): fmt = "yaml"
        elif lp.endswith(".xml"): fmt = "xml"

        export_obj = None
        is_official_config = fmt != "json"
        if fmt == "json":
            try:
                export_obj = json.loads(content)
                is_official_config = ("zabbix_export" in export_obj) or any(k in export_obj for k in ["hosts","templates","groups"])
            except Exception:
                is_official_config = False

        if is_official_config and export_obj is not None:
            created = preload_required_groups_from_export_json(token, export_obj)
            if created:
                print("Vorab angelegte Gruppen:")
                for kind, n, gid in created:
                    print(f" - {kind}: {n} -> {gid}")

        configuration_import_without_maps(token, source_str=content, format_=fmt)
        print("configuration.import (ohne Maps) erfolgreich ausgeführt.")

        if export_obj is not None:
            assign_templates_to_hosts_from_any_export(token, export_obj)

        dash_path = DASHBOARD_FILE_PATH
        if not dash_path or not os.path.exists(dash_path):
            print("Dashboard-Exportdatei nicht gefunden oder nicht gesetzt: dashboard_file_path")
            return

        # Pass the configuration export (if available) for robust submap resolution
        recreate_dashboard_from_file(token, dash_path, extra_export_for_lookup=export_obj)

    finally:
        logout(token)

if __name__ == "__main__":
    main()
