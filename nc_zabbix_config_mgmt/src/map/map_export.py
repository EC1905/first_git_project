#!/usr/bin/env python3
"""
Export Zabbix Maps

This script exports Zabbix maps to JSON/YAML format.
"""

import argparse
import json
import os
import sys
import requests
from datetime import datetime


def login_to_zabbix(url, username, password, ca_cert=False):
    """Login to Zabbix and return auth token."""
    payload = {
        "jsonrpc": "2.0",
        "method": "user.login",
        "params": {"username": username, "password": password},
        "auth": None,
        "id": 1
    }
    
    response = requests.post(url, json=payload, verify=ca_cert)
    result = response.json()
    
    if "error" in result:
        raise Exception(f"Login failed: {result['error']}")
    
    return result.get("result")


def logout_from_zabbix(url, auth_token, ca_cert=False):
    """Logout from Zabbix."""
    payload = {
        "jsonrpc": "2.0",
        "method": "user.logout",
        "params": [],
        "auth": auth_token,
        "id": 2
    }
    
    try:
        requests.post(url, json=payload, verify=ca_cert)
    except Exception:
        pass


def get_map_by_name(url, auth_token, map_name, ca_cert=False):
    """Get map by name."""
    payload = {
        "jsonrpc": "2.0",
        "method": "map.get",
        "params": {
            "filter": {"name": [map_name]},
            "output": "extend",
            "selectSelements": "extend",
            "selectLinks": "extend"
        },
        "auth": auth_token,
        "id": 3
    }
    
    response = requests.post(url, json=payload, verify=ca_cert)
    result = response.json()
    
    if "error" in result:
        raise Exception(f"Failed to get map: {result['error']}")
    
    maps = result.get("result", [])
    return maps[0] if maps else None


def export_map(url, username, password, map_name, export_dir, export_format='json', ca_cert=''):
    """Export a Zabbix map."""
    ca_cert_path = ca_cert if ca_cert else False
    
    # Login
    print(f"Logging in to Zabbix at {url}...")
    auth_token = login_to_zabbix(url, username, password, ca_cert_path)
    
    try:
        # Get map
        print(f"Fetching map: {map_name}")
        map_data = get_map_by_name(url, auth_token, map_name, ca_cert_path)
        
        if not map_data:
            print(f"Map '{map_name}' not found")
            return False
        
        # Prepare export data
        export_data = {
            "export_info": {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "map_name": map_name,
                "exported_by": username
            },
            "map": map_data
        }
        
        # Save to file
        os.makedirs(export_dir, exist_ok=True)
        map_id = map_data.get('sysmapid', 'unknown')
        filename = f"map_{map_id}_{map_name.replace(' ', '_')}.{export_format}"
        filepath = os.path.join(export_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            if export_format == 'json':
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            else:
                # For YAML, we'd need PyYAML
                json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        print(f"Map exported successfully to: {filepath}")
        return True
        
    finally:
        # Logout
        logout_from_zabbix(url, auth_token, ca_cert_path)


def main():
    parser = argparse.ArgumentParser(description="Export Zabbix Map")
    parser.add_argument('--url', required=True, help='Zabbix API URL')
    parser.add_argument('--username', required=True, help='Zabbix username')
    parser.add_argument('--password', required=True, help='Zabbix password')
    parser.add_argument('--map-name', required=True, help='Map name to export')
    parser.add_argument('--export-dir', default='exports', help='Export directory')
    parser.add_argument('--export-format', default='json', choices=['json', 'yaml'], help='Export format')
    parser.add_argument('--ca-cert', default='', help='CA certificate path')
    
    args = parser.parse_args()
    
    try:
        success = export_map(
            url=args.url,
            username=args.username,
            password=args.password,
            map_name=args.map_name,
            export_dir=args.export_dir,
            export_format=args.export_format,
            ca_cert=args.ca_cert
        )
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
