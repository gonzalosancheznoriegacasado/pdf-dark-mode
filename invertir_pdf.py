#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Inversor de PDF a Modo Oscuro - Version Vectorial Nítida (Ultra-HD)
Convierte documentos PDF a modo oscuro sin perder resolución al agrandar o hacer zoom:
- Modo Vectorial NATIVO (100% nítido, zoom infinito sin pixelación).
- Mantiene texto seleccionable y copiable.
- Preserva y adapta colores de diagramas, figuras geométricas y títulos.
- Fallback automático a 300 DPI de ultra alta definición para páginas escaneadas.
"""

import sys
import os
import io
import time
import re
import argparse
import numpy as np
from PIL import Image

try:
    import pymupdf
except ImportError:
    try:
        import fitz as pymupdf
    except ImportError:
        print("[ERROR] No se encontró PyMuPDF. Ejecuta la instalación correspondiente.")
        sys.exit(1)


# ==============================================================================
# 1. Transformación de color para imágenes / escaneos
# ==============================================================================

def transform_image_dark_mode(rgb_arr, pure_black=False):
    """Transforma imagen matricial a modo oscuro inteligente."""
    r = rgb_arr[:, :, 0].astype(np.float32)
    g = rgb_arr[:, :, 1].astype(np.float32)
    b = rgb_arr[:, :, 2].astype(np.float32)

    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    chroma = max_c - min_c
    l = 0.5 * (max_c + min_c)

    if pure_black:
        bg_val = 0.0
        fg_val = 255.0
        tint_b = 0
    else:
        bg_val = 20.0
        fg_val = 237.0
        tint_b = 3

    is_neutral = chroma < 22.0
    out = np.empty_like(rgb_arr)

    l_neutral = l[is_neutral]
    target_neutral = bg_val + (255.0 - l_neutral) * ((fg_val - bg_val) / 255.0)

    # Limpieza de papel escaneado: fondos claros se fuerzan al tono de fondo oscuro
    clean_mask = l_neutral > 238.0
    target_neutral[clean_mask] = bg_val

    val_u8 = np.clip(target_neutral, 0, 255).astype(np.uint8)
    out[is_neutral, 0] = val_u8
    out[is_neutral, 1] = val_u8
    out[is_neutral, 2] = np.clip(val_u8.astype(np.int16) + tint_b, 0, 255).astype(np.uint8)

    is_color = ~is_neutral
    if np.any(is_color):
        r_c = r[is_color]
        g_c = g[is_color]
        b_c = b[is_color]
        l_c = l[is_color]

        target_lc = bg_val + (255.0 - l_c) * ((fg_val - bg_val) / 255.0)
        target_lc = np.maximum(target_lc, 135.0)

        scale = target_lc / np.maximum(l_c, 1.0)
        out[is_color, 0] = np.clip(r_c * scale, 0, 255).astype(np.uint8)
        out[is_color, 1] = np.clip(g_c * scale, 0, 255).astype(np.uint8)
        out[is_color, 2] = np.clip(b_c * scale, 0, 255).astype(np.uint8)

    return out


# ==============================================================================
# 2. Motor Vectorial: Inversión en flujos nativos de PDF (Zoom Infinito)
# ==============================================================================

def invert_rgb_val(r, g, b, bg_val=0.08, fg_val=0.93):
    """Inversión de color RGB vectorial manteniendo matiz y contraste."""
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    chroma = max_c - min_c
    l = 0.5 * (max_c + min_c)

    if chroma < 0.08:
        if l > 0.94:
            return bg_val, bg_val, bg_val + 0.01
        val = bg_val + (1.0 - l) * (fg_val - bg_val)
        return val, val, val
    else:
        target_l = max(bg_val + (1.0 - l) * (fg_val - bg_val), 0.55)
        scale = target_l / max(l, 0.01)
        return min(max(r * scale, 0.0), 1.0), min(max(g * scale, 0.0), 1.0), min(max(b * scale, 0.0), 1.0)


def invert_gray_val(g, bg_val=0.08, fg_val=0.93):
    """Inversión de escala de grises vectorial."""
    if g > 0.94:
        return bg_val
    return bg_val + (1.0 - g) * (fg_val - bg_val)


def invert_cmyk_val(c, m, y, k, bg_val=0.08, fg_val=0.93):
    """Inversión de color CMYK."""
    r = (1.0 - c) * (1.0 - k)
    g = (1.0 - m) * (1.0 - k)
    b = (1.0 - y) * (1.0 - k)
    nr, ng, nb = invert_rgb_val(r, g, b, bg_val, fg_val)
    nk = 1.0 - max(nr, ng, nb)
    if nk < 1.0:
        nc = (1.0 - nr - nk) / (1.0 - nk)
        nm = (1.0 - ng - nk) / (1.0 - nk)
        ny = (1.0 - nb - nk) / (1.0 - nk)
    else:
        nc, nm, ny = 0.0, 0.0, 0.0
    return max(0.0, min(1.0, nc)), max(0.0, min(1.0, nm)), max(0.0, min(1.0, ny)), max(0.0, min(1.0, nk))


def process_vector_page(page, bg_val=0.08, fg_val=0.93, pure_black=False):
    """
    Procesa una página vectorial directamente en sus operadores de PDF.
    Conserva fuentes originales, vectores matemáticos, fórmulas y nitidez absoluta.
    """
    rect = page.rect
    page.clean_contents()
    contents = page.get_contents()
    if not contents:
        return False

    c_xref = contents[0]
    raw_str = page.read_contents().decode('latin1')

    # Rectángulo de fondo oscuro y valores por defecto para texto/trazado no explícito
    bg_color_str = "0.0000 0.0000 0.0000" if pure_black else f"{bg_val:.4f} {bg_val:.4f} {bg_val+0.01:.4f}"
    fg_color_str = "1.0000 1.0000 1.0000" if pure_black else f"{fg_val:.4f} {fg_val:.4f} {fg_val:.4f}"

    bg_stream = (
        f"q {bg_color_str} rg 0 0 {rect.width:.2f} {rect.height:.2f} re f Q "
        f"{fg_color_str} rg {fg_color_str} RG {fg_val:.4f} g {fg_val:.4f} G\n"
    )

    # Tokenizar para aislar cadenas de texto literales ( ... ) o < ... >
    # De esta manera nunca se tocan caracteres que formen parte del texto
    token_pattern = re.compile(r'(\((?:\\.|[^\\)])*\)|<[0-9a-fA-F\s]*>)')
    tokens = token_pattern.split(raw_str)

    # Procesar solo las partes de operadores (índices pares)
    for i in range(0, len(tokens), 2):
        chunk = tokens[i]

        # 1. RGB (rg y RG)
        def repl_rgb(m):
            r, g, b, op = float(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(4)
            nr, ng, nb = invert_rgb_val(r, g, b, bg_val, fg_val)
            return f"{nr:.4f} {ng:.4f} {nb:.4f} {op}"

        chunk = re.sub(
            r'([-+]?(?:\d*\.\d+|\d+))\s+([-+]?(?:\d*\.\d+|\d+))\s+([-+]?(?:\d*\.\d+|\d+))\s+(rg|RG)\b',
            repl_rgb,
            chunk
        )

        # 2. CMYK (k y K)
        def repl_cmyk(m):
            c, m_val, y, k, op = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)), m.group(5)
            nc, nm, ny, nk = invert_cmyk_val(c, m_val, y, k, bg_val, fg_val)
            return f"{nc:.4f} {nm:.4f} {ny:.4f} {nk:.4f} {op}"

        chunk = re.sub(
            r'([-+]?(?:\d*\.\d+|\d+))\s+([-+]?(?:\d*\.\d+|\d+))\s+([-+]?(?:\d*\.\d+|\d+))\s+([-+]?(?:\d*\.\d+|\d+))\s+(k|K)\b',
            repl_cmyk,
            chunk
        )

        # 3. Escala de grises (g y G)
        def repl_gray(m):
            val, op = float(m.group(1)), m.group(2)
            nval = invert_gray_val(val, bg_val, fg_val)
            return f"{nval:.4f} {op}"

        chunk = re.sub(
            r'(?<![0-9.])([-+]?(?:\d*\.\d+|\d+))\s+(g|G)\b',
            repl_gray,
            chunk
        )

        # 4. scn y SCN (color spaces genéricos)
        def repl_scn(m):
            nums = [float(x) for x in m.group(1).split()]
            op = m.group(2)
            if len(nums) == 3:
                nr, ng, nb = invert_rgb_val(nums[0], nums[1], nums[2], bg_val, fg_val)
                return f"{nr:.4f} {ng:.4f} {nb:.4f} {op}"
            elif len(nums) == 1:
                ng = invert_gray_val(nums[0], bg_val, fg_val)
                return f"{ng:.4f} {op}"
            elif len(nums) == 4:
                nc, nm, ny, nk = invert_cmyk_val(nums[0], nums[1], nums[2], nums[3], bg_val, fg_val)
                return f"{nc:.4f} {nm:.4f} {ny:.4f} {nk:.4f} {op}"
            return m.group(0)

        chunk = re.sub(
            r'((?:[-+]?(?:\d*\.\d+|\d+)\s+){1,4})(scn|SCN)\b',
            repl_scn,
            chunk
        )

        tokens[i] = chunk

    transformed_str = bg_stream + ''.join(tokens)
    page.parent.update_stream(c_xref, transformed_str.encode('latin1'))

    # Invertir también imágenes incrustadas dentro de la página vectorial (si las hay)
    try:
        doc = page.parent
        processed_xrefs = set()
        for img_info in page.get_images():
            xref = img_info[0]
            if xref in processed_xrefs:
                continue
            processed_xrefs.add(xref)
            pix = pymupdf.Pixmap(doc, xref)
            if pix.n in (3, 4):
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.h, pix.w, pix.n))
                rgb = arr[:, :, :3]
                dark_rgb = transform_image_dark_mode(rgb, pure_black=pure_black)
                if pix.n == 4:
                    dark_arr = np.dstack([dark_rgb, arr[:, :, 3]])
                else:
                    dark_arr = dark_rgb
                dark_pix = pymupdf.Pixmap(pix.colorspace, pix.irect, dark_arr.tobytes(), pix.alpha)
                page.replace_image(xref, pixmap=dark_pix)
            elif pix.n in (1, 2):
                arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.h, pix.w, pix.n))
                dark_gray = 237 - arr[:, :, 0]
                if pix.n == 2:
                    dark_arr = np.dstack([dark_gray, arr[:, :, 1]])
                else:
                    dark_arr = dark_gray
                dark_pix = pymupdf.Pixmap(pix.colorspace, pix.irect, dark_arr.tobytes(), pix.alpha)
                page.replace_image(xref, pixmap=dark_pix)
    except Exception:
        pass

    return True


# ==============================================================================
# 3. Fallback Raster en Alta Definición (300 DPI) para Escaneos
# ==============================================================================

def process_scanned_page(page, out_doc, dpi=300, pure_black=False):
    """
    Renderiza y procesa páginas que son 100% escaneadas con resolución Retina/Print (300 DPI),
    manteniendo la nitidez incluso con ampliación de zoom.
    """
    orig_rot = page.rotation
    if orig_rot != 0:
        page.set_rotation(0)

    unrotated_rect = page.rect
    words = page.get_text("words")

    zoom = dpi / 72.0
    mat = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    if orig_rot != 0:
        page.set_rotation(orig_rot)

    img_data = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.h, pix.w, pix.n))
    rgb = img_data[:, :, :3]

    dark_rgb = transform_image_dark_mode(rgb, pure_black=pure_black)
    dark_img = Image.fromarray(dark_rgb)

    buf = io.BytesIO()
    dark_img.save(buf, format="PNG", optimize=True)
    img_bytes = buf.getvalue()

    new_page = out_doc.new_page(width=unrotated_rect.width, height=unrotated_rect.height)
    new_page.insert_image(unrotated_rect, stream=img_bytes)

    # Capa de texto invisible para selección y búsqueda
    for w in words:
        w_rect = pymupdf.Rect(w[:4])
        text = w[4]
        fs = max(w_rect.height * 0.85, 3.5)
        baseline_y = w_rect.y1 - w_rect.height * 0.15
        try:
            new_page.insert_text((w_rect.x0, baseline_y), text, fontsize=fs, render_mode=3)
        except Exception:
            pass

    if orig_rot != 0:
        new_page.set_rotation(orig_rot)


# ==============================================================================
# 4. Coordinador de Conversión
# ==============================================================================

def convert_pdf_to_dark(input_path, output_path=None, force_raster=False, dpi=300, pure_black=False):
    """Convierte un archivo PDF a modo oscuro garantizando máxima nitidez."""
    if not os.path.isfile(input_path):
        print(f"[ERROR] El archivo no existe: {input_path}")
        return False

    if not output_path:
        base, _ = os.path.splitext(input_path)
        output_path = f"{base}_dark.pdf"

    print(f"\nProcesando: {os.path.basename(input_path)}")
    print(f"Salida:     {os.path.basename(output_path)}")

    try:
        src_doc = pymupdf.open(input_path)
    except Exception as e:
        print(f"[ERROR] No se pudo abrir el PDF: {e}")
        return False

    total_pages = len(src_doc)
    if total_pages == 0:
        print("[AVISO] El documento está vacío.")
        return False

    bg_val = 0.00 if pure_black else 0.08
    fg_val = 1.00 if pure_black else 0.93

    start_time = time.time()

    # Si se fuerza raster, usamos el doc nuevo con imágenes a 300 DPI
    if force_raster:
        out_doc = pymupdf.open()
        for idx, page in enumerate(src_doc):
            process_scanned_page(page, out_doc, dpi=dpi, pure_black=pure_black)
            pct = int(((idx + 1) / total_pages) * 100)
            bar = "=" * int(25 * (idx + 1) // total_pages) + " " * (25 - int(25 * (idx + 1) // total_pages))
            print(f"\r  [{bar}] {pct}% | Página {idx + 1}/{total_pages} (Raster HD)", end="", flush=True)
        print()
        out_doc.save(output_path, deflate=True, garbage=4)
    else:
        # Modo Híbrido Inteligente: Vectorial por defecto, fallback a Raster HD para escaneos puros
        has_scanned_only = False
        for idx in range(total_pages):
            page = src_doc[idx]
            has_text = bool(page.get_text().strip())
            has_drawings = len(page.get_drawings()) > 0
            if not has_text and not has_drawings:
                has_scanned_only = True
                break

        if has_scanned_only:
            # Documento escaneado: procesar a 300 DPI
            out_doc = pymupdf.open()
            for idx, page in enumerate(src_doc):
                process_scanned_page(page, out_doc, dpi=dpi, pure_black=pure_black)
                pct = int(((idx + 1) / total_pages) * 100)
                bar = "=" * int(25 * (idx + 1) // total_pages) + " " * (25 - int(25 * (idx + 1) // total_pages))
                print(f"\r  [{bar}] {pct}% | Página {idx + 1}/{total_pages} (Raster 300 DPI)", end="", flush=True)
            print()
            out_doc.save(output_path, deflate=True, garbage=4)
        else:
            # Modo Vectorial Nativo: Zoom infinito, nitidez perfecta al 1000%
            for idx in range(total_pages):
                page = src_doc[idx]
                process_vector_page(page, bg_val=bg_val, fg_val=fg_val, pure_black=pure_black)
                pct = int(((idx + 1) / total_pages) * 100)
                bar = "=" * int(25 * (idx + 1) // total_pages) + " " * (25 - int(25 * (idx + 1) // total_pages))
                print(f"\r  [{bar}] {pct}% | Página {idx + 1}/{total_pages} (Vectorial Nítido)", end="", flush=True)
            print()
            src_doc.save(output_path, deflate=True, garbage=4)

    elapsed = time.time() - start_time
    file_size_kb = os.path.getsize(output_path) // 1024

    print(f"[OK] Completado con exito en {elapsed:.1f}s ({file_size_kb} KB)")
    print(f"     Guardado en: {output_path}")
    return True


def select_files_gui():
    """Abre ventana nativa de selección de archivos si se ejecuta sin argumentos."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        files = filedialog.askopenfilenames(
            title="Selecciona uno o más archivos PDF para pasar a Modo Oscuro Nítido",
            filetypes=[("Archivos PDF", "*.pdf"), ("Todos los archivos", "*.*")]
        )
        root.destroy()
        return list(files)
    except Exception:
        return []


def main():
    parser = argparse.ArgumentParser(
        description="Convierte documentos PDF a modo oscuro con nitidez absoluta al ampliar."
    )
    parser.add_argument(
        "archivos",
        nargs="*",
        help="Rutas de los archivos PDF a convertir (o arrastrar encima)."
    )
    parser.add_argument(
        "--raster",
        action="store_true",
        help="Fuerza conversión raster en lugar de vectorial nativo."
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="DPI para páginas escaneadas (por defecto 300 DPI, alta definición)."
    )
    parser.add_argument(
        "--pure-black",
        action="store_true",
        help="Usa fondo negro puro (#000000) en lugar de grafito descansado."
    )

    args = parser.parse_args()
    files_to_process = args.archivos

    print("=" * 60)
    print("   CONVERSOR DE PDF A MODO OSCURO (NITIDEZ TOTAL / VECTORIAL)")
    print("=" * 60)

    if not files_to_process:
        print("\nNo se especifico ningún archivo.")
        print("Abriendo selector de archivos de Windows...")
        files_to_process = select_files_gui()
        if not files_to_process:
            print("\nNo se seleccionó ningún archivo. Operación cancelada.")
            return

    success_count = 0
    for file_path in files_to_process:
        if file_path.lower().endswith(".pdf"):
            if convert_pdf_to_dark(
                file_path,
                force_raster=args.raster,
                dpi=args.dpi,
                pure_black=args.pure_black
            ):
                success_count += 1
        else:
            print(f"[OMITIDO] No es un archivo PDF: {file_path}")

    print("\n" + "=" * 60)
    print(f"Resumen: {success_count} de {len(files_to_process)} archivo(s) procesado(s) correctamente.")
    print("=" * 60)


if __name__ == "__main__":
    main()
