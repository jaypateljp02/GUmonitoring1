import os
from datetime import datetime, timedelta
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch

def generate_telemetry_pdf(device_id: str, room_name: str, sensor_type: str, start_time: datetime, end_time: datetime, logs: list) -> BytesIO:
    """
    Generates a beautifully formatted PDF report for sensor telemetry data.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40
    )
    
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#1E293B'),
        spaceAfter=15
    )
    
    h2_style = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=colors.HexColor('#2563EB'),
        spaceBefore=10,
        spaceAfter=8
    )
    
    meta_label_style = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#475569')
    )
    
    meta_val_style = ParagraphStyle(
        'MetaValue',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#0F172A')
    )
    
    body_style = ParagraphStyle(
        'TableBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#1E293B')
    )
    
    header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        textColor=colors.white
    )

    story = []
    
    # Title
    story.append(Paragraph("Ground Up Cold Storage Telemetry Report", title_style))
    story.append(Spacer(1, 10))
    
    # Metadata Table
    ist_offset = timedelta(hours=5, minutes=30)
    start_ist = (start_time + ist_offset).strftime('%Y-%m-%d %I:%M %p')
    end_ist = (end_time + ist_offset).strftime('%Y-%m-%d %I:%M %p')
    
    meta_data = [
        [
            Paragraph("Room / Device:", meta_label_style), Paragraph(f"{room_name} ({device_id})", meta_val_style),
            Paragraph("Report Date:", meta_label_style), Paragraph(datetime.now().strftime('%Y-%m-%d'), meta_val_style)
        ],
        [
            Paragraph("Timeframe (IST):", meta_label_style), Paragraph(f"{start_ist} to {end_ist}", meta_val_style),
            Paragraph("Metric Type:", meta_label_style), Paragraph(sensor_type.capitalize(), meta_val_style)
        ]
    ]
    
    meta_table = Table(meta_data, colWidths=[100, 180, 100, 150])
    meta_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 0),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 15))
    
    # Calculate Summary Stats
    temps = [float(log.temperature) for log in logs if log.temperature is not None]
    hums = [float(log.humidity) for log in logs if log.humidity is not None]
    
    t_avg = round(sum(temps) / len(temps), 2) if temps else "N/A"
    t_min = min(temps) if temps else "N/A"
    t_max = max(temps) if temps else "N/A"
    
    h_avg = round(sum(hums) / len(hums), 2) if hums else "N/A"
    h_min = min(hums) if hums else "N/A"
    h_max = max(hums) if hums else "N/A"
    
    story.append(Paragraph("Key Summary Metrics", h2_style))
    
    summary_headers = ["Metric", "Average", "Minimum", "Maximum"]
    summary_rows = []
    
    if sensor_type in ["temperature", "both", "all"]:
        summary_rows.append(["Temperature (°C)", f"{t_avg} °C", f"{t_min} °C", f"{t_max} °C"])
    if sensor_type in ["humidity", "both", "all"]:
        summary_rows.append(["Humidity (%)", f"{h_avg} %", f"{h_min} %", f"{h_max} %"])
        
    summary_data = [[Paragraph(h, header_style) for h in summary_headers]]
    for r in summary_rows:
        summary_data.append([Paragraph(str(val), body_style) for val in r])
        
    summary_table = Table(summary_data, colWidths=[150, 120, 120, 120])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2563EB')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E2E8F0')),
        ('BACKGROUND', (0,1), (-1,-1), colors.HexColor('#F8FAFC')),
    ]))
    
    story.append(summary_table)
    story.append(Spacer(1, 20))
    
    # Detailed Logs Table
    story.append(Paragraph("Detailed Telemetry Log", h2_style))
    
    log_headers = ["Timestamp (IST)", "Timestamp (UTC)", "Temp (°C)", "Humidity (%)", "Battery (%)"]
    log_data = [[Paragraph(h, header_style) for h in log_headers]]
    
    # Order oldest first
    sorted_logs = sorted(logs, key=lambda x: x.timestamp)
    
    for idx, log in enumerate(sorted_logs):
        utc_str = log.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        ist_str = (log.timestamp + ist_offset).strftime('%Y-%m-%d %I:%M:%S %p')
        t_val = f"{float(log.temperature):.2f} °C" if log.temperature is not None else "--"
        h_val = f"{float(log.humidity):.2f} %" if log.humidity is not None else "--"
        b_val = f"{int(log.battery_level)}%" if log.battery_level is not None else "--"
        
        row_cells = [
            Paragraph(ist_str, body_style),
            Paragraph(utc_str, body_style),
            Paragraph(t_val, body_style),
            Paragraph(h_val, body_style),
            Paragraph(b_val, body_style),
        ]
        log_data.append(row_cells)
        
    log_table = Table(log_data, colWidths=[150, 140, 80, 80, 80])
    
    # Grid and alternating row backgrounds
    table_style = TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E293B')),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#CBD5E1')),
    ])
    
    for i in range(1, len(log_data)):
        bg_color = colors.HexColor('#F8FAFC') if i % 2 == 0 else colors.white
        table_style.add('BACKGROUND', (0, i), (-1, i), bg_color)
        
    log_table.setStyle(table_style)
    story.append(log_table)
    
    # Page template header/footer decorator
    def add_page_decorations(canvas, doc):
        canvas.saveState()
        # Header
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#64748B'))
        canvas.drawString(40, letter[1] - 25, "Ground Up Food & Fermentation Factory — IoT Monitoring Services")
        canvas.setStrokeColor(colors.HexColor('#E2E8F0'))
        canvas.setLineWidth(0.5)
        canvas.line(40, letter[1] - 30, letter[0] - 40, letter[1] - 30)
        
        # Footer
        canvas.line(40, 40, letter[0] - 40, 40)
        canvas.drawString(40, 25, f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        canvas.drawRightString(letter[0] - 40, 25, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()
        
    doc.build(story, onFirstPage=add_page_decorations, onLaterPages=add_page_decorations)
    buffer.seek(0)
    return buffer
