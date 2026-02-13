#!/usr/bin/env python3
"""
eero Business Dashboard - Report Generation
CSV and PDF export for network metrics and uptime data.
"""
import csv
import io
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def generate_report_data(data_cache: dict) -> dict:
    """
    Aggregate report data from the current cache.
    Returns a dict with network summaries and combined stats.
    """
    networks = data_cache.get('networks', {})
    combined = data_cache.get('combined', {})
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'total_networks': len(networks),
        'total_devices': combined.get('total_devices', 0),
        'wireless_devices': combined.get('wireless_devices', 0),
        'wired_devices': combined.get('wired_devices', 0),
        'networks': [],
    }

    for nid, ncache in networks.items():
        report['networks'].append({
            'network_id': nid,
            'total_devices': ncache.get('total_devices', 0),
            'wireless_devices': ncache.get('wireless_devices', 0),
            'wired_devices': ncache.get('wired_devices', 0),
            'health_status': ncache.get('health_status', 'unknown'),
            'bandwidth_utilization': ncache.get('bandwidth_utilization', 0.0),
            'uptime_24h': ncache.get('uptime_24h', 100.0),
            'last_update': ncache.get('last_update', ''),
        })

    return report


def generate_csv(report_data: dict) -> str:
    """Generate CSV string from report data."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(['eero Business Dashboard Report'])
    writer.writerow(['Generated', report_data['generated_at']])
    writer.writerow([])

    # Summary
    writer.writerow(['Summary'])
    writer.writerow(['Total Networks', report_data['total_networks']])
    writer.writerow(['Total Devices', report_data['total_devices']])
    writer.writerow(['Wireless Devices', report_data['wireless_devices']])
    writer.writerow(['Wired Devices', report_data['wired_devices']])
    writer.writerow([])

    # Per-network details
    writer.writerow(['Network Details'])
    writer.writerow([
        'Network ID', 'Devices', 'Wireless', 'Wired',
        'Health', 'Bandwidth %', 'Uptime 24h %', 'Last Update'
    ])
    for n in report_data.get('networks', []):
        writer.writerow([
            n['network_id'],
            n['total_devices'],
            n['wireless_devices'],
            n['wired_devices'],
            n['health_status'],
            n['bandwidth_utilization'],
            n['uptime_24h'],
            n['last_update'],
        ])

    return output.getvalue()


def generate_pdf(report_data: dict) -> bytes:
    """Generate PDF bytes from report data using reportlab."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        logger.error("reportlab not installed — PDF export unavailable")
        return b''

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    # Title
    elements.append(Paragraph('eero Business Dashboard Report', styles['Title']))
    elements.append(Paragraph(f"Generated: {report_data['generated_at']}", styles['Normal']))
    elements.append(Spacer(1, 20))

    # Summary table
    summary_data = [
        ['Metric', 'Value'],
        ['Total Networks', str(report_data['total_networks'])],
        ['Total Devices', str(report_data['total_devices'])],
        ['Wireless Devices', str(report_data['wireless_devices'])],
        ['Wired Devices', str(report_data['wired_devices'])],
    ]
    t = Table(summary_data, colWidths=[200, 200])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003D5C')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 20))

    # Network details table
    elements.append(Paragraph('Network Details', styles['Heading2']))
    net_header = ['Network ID', 'Devices', 'Health', 'BW %', 'Uptime %']
    net_rows = [net_header]
    for n in report_data.get('networks', []):
        net_rows.append([
            n['network_id'],
            str(n['total_devices']),
            n['health_status'],
            f"{n['bandwidth_utilization']:.1f}",
            f"{n['uptime_24h']:.1f}",
        ])
    t2 = Table(net_rows, colWidths=[120, 60, 80, 60, 60])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#003D5C')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
    ]))
    elements.append(t2)

    doc.build(elements)
    return buffer.getvalue()
