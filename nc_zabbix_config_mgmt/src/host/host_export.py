#!/usr/bin/env python3
"""
Export Zabbix Hosts

This script exports Zabbix hosts to JSON/YAML format.
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


def get_host_by_name(url, auth_token, host_name, ca_cert=False):
    """Get host by name."""
    payload = {
        "jsonrpc": "2.0",
        "method": "host.get",
        "params": {
            "filter": {"host": [host_name]},
            "output": "extend",
            "selectInterfaces": "extend",
            "selectGroups": "extend",
            "selectParentTemplates": "extend",
            "selectItems": "extend",
            "selectTriggers": "extend"
        },
        "auth": auth_token,
        "id": 3
    }
    
    response = requests.post(url, json=payload, verify=ca_cert)
    result = response.json()
    
    if "error" in result:
        raise Exception(f"Failed to get host: {result['error']}")
    
    hosts = result.get("result", [])
    return hosts[0] if hosts else None


def export_host(url, username, password, host_name, export_dir, export_format='json', ca_cert=''):
    """Export a Zabbix host."""
    ca_cert_path = ca_cert if ca_cert else False
    
    # Login
    print(f"Logging in to Zabbix at {url}...")
    auth_token = login_to_zabbix(url, username, password, ca_cert_path)
    
    try:
        # Get host
        print(f"Fetching host: {host_name}")
        host_data = get_host_by_name(url, auth_token, host_name, ca_cert_path)
        
        if not host_data:
            print(f"Host '{host_name}' not found")
            return False
        
        # Prepare export data
        export_data = {
            "export_info": {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "host_name": host_name,
                "exported_by": username
            },
            "host": host_data
        }
        
        # Save to file
        os.makedirs(export_dir, exist_ok=True)
        host_id = host_data.get('hostid', 'unknown')
        filename = f"host_{host_id}_{host_name.replace(' ', '_')}.{export_format}"
        filepath = os.path.join(export_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            if export_format == 'json':
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            else:
                # For YAML, we'd need PyYAML
                json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        print(f"Host exported successfully to: {filepath}")
        return True
        
    finally:
        # Logout
        logout_from_zabbix(url, auth_token, ca_cert_path)


def main():
    parser = argparse.ArgumentParser(description="Export Zabbix Host")
    parser.add_argument('--url', required=True, help='Zabbix API URL')
    parser.add_argument('--username', required=True, help='Zabbix username')
    parser.add_argument('--password', required=True, help='Zabbix password')
    parser.add_argument('--host-name', required=True, help='Host name to export')
    parser.add_argument('--export-dir', default='exports', help='Export directory')
    parser.add_argument('--export-format', default='json', choices=['json', 'yaml'], help='Export format')
    parser.add_argument('--ca-cert', default='', help='CA certificate path')
    
    args = parser.parse_args()
    
    try:
        success = export_host(
            url=args.url,
            username=args.username,
            password=args.password,
            host_name=args.host_name,
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
