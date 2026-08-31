#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日静态释放地址排名 Excel 报表。"""

import html
import io
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta

from db import get_conn


def get_top_static_release_rows(date_str, limit=30):
    """返回排名后的逐笔明细；排名按地址当日静态释放合计计算。"""
    next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT id, to_addr, value
        FROM events
        WHERE type='release_static'
          AND timestamp >= ? AND timestamp < ?
        ORDER BY timestamp ASC, block ASC, id ASC
        """,
        (date_str + " 00:00:00", next_date + " 00:00:00"),
    ).fetchall()
    conn.close()

    grouped = defaultdict(list)
    for row in rows:
        address = (row["to_addr"] or "").strip().lower()
        if address:
            grouped[address].append(float(row["value"] or 0))

    ranked = sorted(
        grouped.items(),
        key=lambda item: (-sum(item[1]), item[0]),
    )[:limit]

    result = []
    for rank, (address, amounts) in enumerate(ranked, 1):
        for amount in amounts:
            result.append((rank, address, amount))
    return result


def _cell(value, row, col, style=None):
    ref = f"{col}{row}"
    style_attr = f' s="{style}"' if style is not None else ""
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    escaped = html.escape(str(value), quote=False)
    return f'<c r="{ref}"{style_attr} t="inlineStr"><is><t>{escaped}</t></is></c>'


def build_xlsx(date_str, rows):
    """生成无额外颜色填充的标准 xlsx 文件。"""
    sheet_rows = [_cell("排名", 1, "A", 1) + _cell("用户地址", 1, "B", 1) + _cell("静态释放数量", 1, "C", 1)]
    for excel_row, (rank, address, amount) in enumerate(rows, 2):
        sheet_rows.append(
            _cell(rank, excel_row, "A")
            + _cell(address, excel_row, "B")
            + _cell(round(amount, 2), excel_row, "C")
        )
    last_row = max(1, len(sheet_rows))
    sheet_data = "".join(
        '<row r="%s">%s</row>' % (i, content)
        for i, content in enumerate(sheet_rows, 1)
    )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:C{last_row}"/><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<cols><col min="1" max="1" width="8"/><col min="2" max="2" width="48"/><col min="3" max="3" width="18"/></cols>'
        f'<sheetData>{sheet_data}</sheetData>'
        f'<autoFilter ref="A1:C{last_row}"/>'
        '</worksheet>'
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0"/></cellXfs>'
        '</styleSheet>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )
    workbook_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="静态释放前30名" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    workbook_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                     '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in {
            "[Content_Types].xml": content_types,
            "_rels/.rels": rels,
            "xl/workbook.xml": workbook_xml,
            "xl/_rels/workbook.xml.rels": workbook_rels,
            "xl/worksheets/sheet1.xml": sheet_xml,
            "xl/styles.xml": styles_xml,
        }.items():
            archive.writestr(name, content)
    output.seek(0)
    return output
