"""Local table geometry; no file-specific coordinates or external inference."""
from collections import deque
import math

import numpy as np
from PIL import Image, ImageDraw


def rectify_paper(gray):
    """Flatten a bright paper quadrilateral; keep the image if uncertain."""
    small = gray.copy()
    small.thumbnail((200, 300))
    mask = np.asarray(small) > 155
    seen = np.zeros(mask.shape, dtype=bool)
    largest = []
    height, width = mask.shape
    for y, x in zip(*np.where(mask)):
        if seen[y, x]:
            continue
        pending = deque([(int(x), int(y))])
        seen[y, x] = True
        points = []
        while pending:
            px, py = pending.popleft()
            points.append((px, py))
            for nx, ny in ((px-1, py), (px+1, py), (px, py-1), (px, py+1)):
                if 0 <= nx < width and 0 <= ny < height and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    pending.append((nx, ny))
        if len(points) > len(largest):
            largest = points
    if len(largest) < width * height * .12:
        return gray
    points = np.asarray(largest, dtype=float)
    corners = points[[np.argmin(points.sum(1)), np.argmin(points[:, 1]-points[:, 0]),
                      np.argmax(points.sum(1)), np.argmax(points[:, 1]-points[:, 0])]]
    corners *= [gray.width / width, gray.height / height]
    tl, tr, br, bl = corners
    area = abs(sum(corners[i, 0]*corners[(i+1)%4, 1]-corners[(i+1)%4, 0]*corners[i, 1] for i in range(4))) / 2
    if area < gray.width * gray.height * .35:
        return gray
    if area > gray.width * gray.height * .93:
        return gray
    target_w = round(max(math.dist(tl, tr), math.dist(bl, br)))
    target_h = round(max(math.dist(tl, bl), math.dist(tr, br)))
    if min(target_w, target_h) < 200:
        return gray
    equations, results = [], []
    for (x, y), (sx, sy) in zip([(0, 0), (target_w, 0), (target_w, target_h), (0, target_h)], corners):
        equations.extend([[x,y,1,0,0,0,-sx*x,-sx*y], [0,0,0,x,y,1,-sy*x,-sy*y]])
        results.extend([sx, sy])
    try:
        coefficients = np.linalg.solve(equations, results)
    except np.linalg.LinAlgError:
        return gray
    return gray.transform((target_w, target_h), Image.Transform.PERSPECTIVE, coefficients,
                          Image.Resampling.BICUBIC, fillcolor=255)


def ruling_segments(gray):
    """Detect long dark runs, retaining their spans for merged-cell decisions."""
    dark = np.asarray(gray) < 145
    horizontal, vertical = [], []
    for data, minimum, output in ((dark, max(40, gray.width//40), horizontal),
                                  (dark.T, max(35, gray.height//25), vertical)):
        for index, row in enumerate(data):
            padded = np.r_[False, row, False].astype(np.int8)
            edges = np.diff(padded)
            for start, end in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
                if end-start >= minimum:
                    output.append((int(index), int(start), int(end)))
    return horizontal, vertical


def erase_rulings(gray, horizontal, vertical):
    clean = gray.copy()
    draw = ImageDraw.Draw(clean)
    for y, x0, x1 in horizontal:
        draw.line((x0, y, x1, y), fill=255, width=2)
    for x, y0, y1 in vertical:
        draw.line((x, y0, x, y1), fill=255, width=2)
    return clean


def flatten_menu_rows(gray, horizontal, return_strips=False):
    """Unbend photographed table rows into separated strips for local OCR."""
    groups = []
    for line in sorted(horizontal):
        if groups and line[0] - groups[-1][-1][0] <= 8:
            groups[-1].append(line)
        else:
            groups.append([line])
    groups = [g for g in groups if max(t[2] for t in g)-min(t[1] for t in g) > gray.width*.45]
    if not 12 <= len(groups) <= 120:
        return None
    curves = []
    for group in groups:
        xs, ys = [], []
        for y, left, right in group:
            xs.extend([left, (left+right)/2, right])
            ys.extend([y,y,y])
        curves.append(np.polyval(np.polyfit(xs, ys, 2), np.arange(gray.width)))
    pixels = np.asarray(gray)
    strips = []
    for top, bottom in zip(curves, curves[1:]):
        height = int(np.median(bottom-top))-8
        if not 15 <= height <= 150:
            continue
        ys = top[None, :] + 4 + np.linspace(0, 1, height)[:, None] * (bottom-top-8)[None, :]
        ys = np.clip(ys, 0, gray.height-1).astype(int)
        strip = pixels[ys, np.arange(gray.width)[None, :]].copy()
        strips.append(Image.fromarray(strip))
    total = sum(s.height+40 for s in strips)
    if not strips or total * gray.width > 20_000_000:
        return None
    if return_strips:
        return strips
    canvas = Image.new('L', (gray.width, total+40), 255)
    offset = 20
    for strip in strips:
        canvas.paste(strip, (0, offset))
        offset += strip.height+40
    return canvas
