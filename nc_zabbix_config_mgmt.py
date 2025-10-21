import argparse
import subprocess
import sys
import json
import os
import logging
import shutil
from datetime import datetime
from pathlib import Path

from nc_zabbix_config_mgmt.config import load_config
from nc_zabbix_config_mgmt_source.config.config_loader import load_source_config
from nc_zabbix_config_mgmt_target.config import load_target_config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def export_all_items(config_path, export_base_path):
    """
    Export all dashboards, maps, and hosts defined in the configuration file.
    
    Args:
        config_path: Path to the JSON configuration file
        export_base_path: Base path where exports should be stored
    """
    logger.info(f"Loading configuration from: {config_path}")
    
    # Load the configuration
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except FileNotFoundError:
        logger.error(f"Configuration file not found: {config_path}")
        return False
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in configuration file: {e}")
        return False
    
    # Extract configuration values
    url = config.get('url')
    username = config.get('username')
    password = config.get('password')
    ca_certificate_path = config.get('ca_certificate_path', '')
    export_format = config.get('export_format', 'json')
    
    dashboards = config.get('dashboards', [])
    maps = config.get('maps', [])
    hosts = config.get('hosts', [])
    
    # Create export directory structure
    export_dir = Path(export_base_path) / 'nc_zabbix_export'
    export_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Export directory: {export_dir}")
    logger.info(f"Zabbix URL: {url}")
    logger.info(f"Username: {username}")
    logger.info(f"Dashboards to export: {len(dashboards)}")
    logger.info(f"Maps to export: {len(maps)}")
    logger.info(f"Hosts to export: {len(hosts)}")
    
    # Track results
    results = {
        'dashboards': {'success': 0, 'failed': 0},
        'maps': {'success': 0, 'failed': 0},
        'hosts': {'success': 0, 'failed': 0}
    }
    
    # Export dashboards
    for dashboard in dashboards:
        dashboard_name = dashboard.get('name') if isinstance(dashboard, dict) else dashboard
        logger.info(f"Exporting dashboard: {dashboard_name}")
        
        try:
            cmd = [
                sys.executable,
                'nc_zabbix_config_mgmt/src/dashboard/export_dashboard.py',
                '--url', url,
                '--username', username,
                '--password', password,
                '--dashboard-name', str(dashboard_name),
                '--export-dir', str(export_dir),
                '--export-format', export_format
            ]
            
            if ca_certificate_path:
                cmd.extend(['--ca-cert', ca_certificate_path])
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"✓ Dashboard '{dashboard_name}' exported successfully")
                results['dashboards']['success'] += 1
            else:
                logger.error(f"✗ Dashboard '{dashboard_name}' export failed: {result.stderr}")
                results['dashboards']['failed'] += 1
                
        except Exception as e:
            logger.error(f"✗ Error exporting dashboard '{dashboard_name}': {e}")
            results['dashboards']['failed'] += 1
    
    # Export maps
    for map_item in maps:
        map_name = map_item.get('name') if isinstance(map_item, dict) else map_item
        logger.info(f"Exporting map: {map_name}")
        
        try:
            cmd = [
                sys.executable,
                'nc_zabbix_config_mgmt/src/map/map_export.py',
                '--url', url,
                '--username', username,
                '--password', password,
                '--map-name', str(map_name),
                '--export-dir', str(export_dir),
                '--export-format', export_format
            ]
            
            if ca_certificate_path:
                cmd.extend(['--ca-cert', ca_certificate_path])
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"✓ Map '{map_name}' exported successfully")
                results['maps']['success'] += 1
            else:
                logger.error(f"✗ Map '{map_name}' export failed: {result.stderr}")
                results['maps']['failed'] += 1
                
        except Exception as e:
            logger.error(f"✗ Error exporting map '{map_name}': {e}")
            results['maps']['failed'] += 1
    
    # Export hosts
    for host_item in hosts:
        host_name = host_item.get('name') if isinstance(host_item, dict) else host_item
        logger.info(f"Exporting host: {host_name}")
        
        try:
            cmd = [
                sys.executable,
                'nc_zabbix_config_mgmt/src/host/host_export.py',
                '--url', url,
                '--username', username,
                '--password', password,
                '--host-name', str(host_name),
                '--export-dir', str(export_dir),
                '--export-format', export_format
            ]
            
            if ca_certificate_path:
                cmd.extend(['--ca-cert', ca_certificate_path])
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode == 0:
                logger.info(f"✓ Host '{host_name}' exported successfully")
                results['hosts']['success'] += 1
            else:
                logger.error(f"✗ Host '{host_name}' export failed: {result.stderr}")
                results['hosts']['failed'] += 1
                
        except Exception as e:
            logger.error(f"✗ Error exporting host '{host_name}': {e}")
            results['hosts']['failed'] += 1
    
    # Print summary
    logger.info("\n" + "="*60)
    logger.info("EXPORT SUMMARY")
    logger.info("="*60)
    logger.info(f"Dashboards: {results['dashboards']['success']} succeeded, {results['dashboards']['failed']} failed")
    logger.info(f"Maps: {results['maps']['success']} succeeded, {results['maps']['failed']} failed")
    logger.info(f"Hosts: {results['hosts']['success']} succeeded, {results['hosts']['failed']} failed")
    logger.info("="*60)
    
    # Create ZIP archive
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_filename = f"nc_zabbix_export_{timestamp}"
    zip_path = Path(export_base_path) / zip_filename
    
    logger.info(f"\nCreating ZIP archive: {zip_path}.zip")
    
    try:
        shutil.make_archive(str(zip_path), 'zip', export_dir)
        logger.info(f"✓ ZIP archive created successfully: {zip_path}.zip")
    except Exception as e:
        logger.error(f"✗ Failed to create ZIP archive: {e}")
        return False
    
    total_failed = results['dashboards']['failed'] + results['maps']['failed'] + results['hosts']['failed']
    return total_failed == 0


def main():
    parser = argparse.ArgumentParser(description="Zabbix Config Management Tool")
    
    # Check if new arguments are provided
    if '--config' in sys.argv or '--export' in sys.argv:
        # New mode: batch export with config file
        parser.add_argument('--config', required=True, help='Path to JSON configuration file')
        parser.add_argument('--export', required=True, help='Base path for export files')
        args = parser.parse_args()
        
        success = export_all_items(args.config, args.export)
        sys.exit(0 if success else 1)
    
    # Original mode: single item export/import
    parser.add_argument("action", choices=["import", "export"], help="Aktion: import oder export")
    parser.add_argument("type", choices=["dashboard", "map", "host"], help="Objekttyp: dashboard, map, host")
    args = parser.parse_args()

    dashboard_config = load_config("dashboard_config.json")
    print("\n=== Dashboard-Config ===")
    for key, value in dashboard_config.items():
        print(f"{key}: {value}")

    source_config = load_source_config("nc_zabbix_config_mgmt_source/config/config.json")
    print("\n=== Source-Config ===")
    for key, value in source_config.items():
        print(f"{key}: {value}")

    target_config = load_target_config("nc_zabbix_config_mgmt_target/config/config.json")
    print("\n=== Target-Config ===")
    for key, value in target_config.items():
        print(f"{key}: {value}")

    if args.action == "export":
        if args.type == "dashboard":
            subprocess.run([sys.executable, "nc_zabbix_config_mgmt/src/dashboard/export_dashboard.py"])
        elif args.type == "map":
            subprocess.run([sys.executable, "nc_zabbix_config_mgmt/src/map/map_export.py"])
        elif args.type == "host":
            subprocess.run([sys.executable, "nc_zabbix_config_mgmt/src/host/host_export.py"])

    elif args.action == "import":
        if args.type == "dashboard":
            subprocess.run([sys.executable, "nc_zabbix_config_mgmt/src/dashboard/import_dashboard.py"])
        elif args.type == "map":
            subprocess.run([sys.executable, "nc_zabbix_config_mgmt/src/map/map_import.py"])
        elif args.type == "host":
            subprocess.run([sys.executable, "nc_zabbix_config_mgmt/src/host/host_import.py"])


if __name__ == "__main__":
    main()
