"""動作確認用のサンプル教材PDFを生成する（著作権の心配がない自作テキスト）。

    python scripts/make_sample_pdf.py tests/fixtures/sample.pdf
"""
import sys
from pathlib import Path

import pymupdf

FONT = "japan"  # PyMuPDF 内蔵の日本語フォント


def header_footer(page, n):
    page.insert_text((50, 30), "IT基礎テキスト", fontname=FONT, fontsize=8)
    page.insert_text((290, 820), f"- {n} -", fontname=FONT, fontsize=9)


def body(page, y, lines, size=10.5):
    for line in lines:
        page.insert_text((60, y), line, fontname=FONT, fontsize=size)
        y += size * 1.8
    return y


def table(page, x, y, rows, colw=(120, 200), rowh=22):
    for r, row in enumerate(rows):
        cx = x
        for c, cell in enumerate(row):
            rect = pymupdf.Rect(cx, y + r * rowh, cx + colw[c], y + (r + 1) * rowh)
            page.draw_rect(rect, color=(0, 0, 0), width=0.8)
            page.insert_text((rect.x0 + 6, rect.y1 - 7), cell, fontname=FONT, fontsize=10)
            cx += colw[c]
    return y + len(rows) * rowh


def build(out: Path) -> None:
    doc = pymupdf.open()
    doc.set_metadata({"title": "IT基礎テキスト", "author": "サンプル著者"})

    # p1 表紙
    p = doc.new_page(width=595, height=842)
    p.insert_text((150, 300), "IT基礎テキスト", fontname=FONT, fontsize=28)
    p.insert_text((220, 360), "サンプル著者", fontname=FONT, fontsize=14)

    # p2 第1章
    p = doc.new_page(width=595, height=842)
    header_footer(p, 2)
    p.insert_text((60, 90), "第1章 ネットワークの基礎", fontname=FONT, fontsize=20)
    p.insert_text((60, 140), "1.1 ネットワークとは", fontname=FONT, fontsize=14)
    y = body(p, 170, [
        "ネットワークとは、複数のコンピュータを接続し、データをやり取りする仕組みである。",
        "TCP/IPは、インターネットにおける通信プロトコルの標準である。",
        "・LANは限られた範囲のネットワーク",
        "・WANは広い範囲を結ぶネットワーク",
    ])
    p.insert_text((60, y + 20), "1.2 IPアドレス", fontname=FONT, fontsize=14)
    y = body(p, y + 50, [
        "IPv4アドレスは32ビットで表され、約43億個のアドレスを表現できる。",
        "アドレス数 = 2^32 = 4,294,967,296",
    ])
    table(p, 60, y + 10, [["種類", "ビット数"], ["IPv4", "32ビット"], ["IPv6", "128ビット"]])

    # p3 第1章 続き
    p = doc.new_page(width=595, height=842)
    header_footer(p, 3)
    p.insert_text((60, 90), "1.3 DNS", fontname=FONT, fontsize=14)
    body(p, 120, [
        "DNSは、ドメイン名とIPアドレスを対応付ける仕組みである。",
        "Webブラウザは、URLに含まれるドメイン名をDNSで問い合わせる。",
    ])

    # p4 第2章
    p = doc.new_page(width=595, height=842)
    header_footer(p, 4)
    p.insert_text((60, 90), "第2章 データベースの基礎", fontname=FONT, fontsize=20)
    p.insert_text((60, 140), "2.1 SQLとは", fontname=FONT, fontsize=14)
    body(p, 170, [
        "SQLは、関係データベースを操作するための言語である。",
        "SELECT文でデータを検索し、INSERT文でデータを追加する。",
    ])

    # p5 スキャンページ（文字を画像化してテキスト層なし）
    tmp = pymupdf.open()
    tp = tmp.new_page(width=595, height=842)
    tp.insert_text((60, 90), "2.2 正規化", fontname=FONT, fontsize=14)
    body(tp, 120, ["正規化とは、データの重複を取り除き、整合性を保つための設計手法である。"])
    pix = tp.get_pixmap(dpi=150)
    p = doc.new_page(width=595, height=842)
    p.insert_image(p.rect, pixmap=pix)

    doc.set_toc([
        [1, "第1章 ネットワークの基礎", 2],
        [2, "1.1 ネットワークとは", 2],
        [2, "1.2 IPアドレス", 2],
        [2, "1.3 DNS", 3],
        [1, "第2章 データベースの基礎", 4],
        [2, "2.1 SQLとは", 4],
        [2, "2.2 正規化", 5],
    ])
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    print("作成:", out)


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/sample.pdf"))
