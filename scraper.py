# scraper.py
import json
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://pokemongo.com"
NEWS_URL = f"{BASE_URL}/es/news"
OUTPUT_PATH = "data/community_day.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GitHubActionBot/1.0)"
}

SPANISH_MONTHS = {
    "enero": "01",
    "febrero": "02",
    "marzo": "03",
    "abril": "04",
    "mayo": "05",
    "junio": "06",
    "julio": "07",
    "agosto": "08",
    "septiembre": "09",
    "setiembre": "09",
    "octubre": "10",
    "noviembre": "11",
    "diciembre": "12",
}


def fetch_html(url: str) -> BeautifulSoup:
    res = requests.get(url, headers=HEADERS, timeout=30)
    res.raise_for_status()
    return BeautifulSoup(res.text, "lxml")


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_community_day_title(title: str) -> bool:
    title_norm = normalize_spaces(title).lower()
    return "día de la comunidad" in title_norm


def parse_spanish_date(fecha_raw: str) -> str:
    text = normalize_spaces(fecha_raw).lower()
    match = re.search(r"(\d{1,2}) de ([a-záéíóú]+) de (\d{4})", text, re.IGNORECASE)
    if not match:
        return fecha_raw

    day, month_name, year = match.groups()
    month = SPANISH_MONTHS.get(month_name.lower())
    if not month:
        return fecha_raw

    return f"{year}-{month}-{int(day):02d}"


def extract_event_datetime(fulltext: str) -> tuple[str, str, str]:
    text = normalize_spaces(fulltext)

    # Busca frases tipo:
    # "el 17 de mayo de 2026, de 14:00 a 17:00"
    # "17 de mayo de 2026 ... 14:00 ... 17:00"
    match = re.search(
        r"(\d{1,2} de [a-zA-ZáéíóúñÑ]+ de \d{4}).{0,120}?(\d{1,2}:\d{2}).{0,40}?(?:a|-|–|al)\s*(\d{1,2}:\d{2})",
        text,
        re.IGNORECASE,
    )
    if match:
        fecha_raw, hora_inicio, hora_fin = match.groups()
        return parse_spanish_date(fecha_raw), hora_inicio, hora_fin

    return "", "", ""


def get_json_ld_blocks(soup: BeautifulSoup) -> list[dict]:
    blocks = []

    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, list):
            blocks.extend([x for x in parsed if isinstance(x, dict)])
        elif isinstance(parsed, dict):
            blocks.append(parsed)

    return blocks


def get_community_day_article() -> dict | None:
    soup = fetch_html(NEWS_URL)

    # 1) Fuente principal: JSON-LD, mucho más estable que las clases CSS.
    for block in get_json_ld_blocks(soup):
        if block.get("@type") != "ItemList":
            continue

        for item in block.get("itemListElement", []):
            if not isinstance(item, dict):
                continue

            title = normalize_spaces(item.get("name", ""))
            url = item.get("url", "")

            if is_community_day_title(title) and url:
                return {
                    "title": title,
                    "relative_url": url.replace(BASE_URL, "") if url.startswith(BASE_URL) else url,
                    "url": urljoin(BASE_URL, url),
                    "image": None,
                    "date_published": None,
                }

    # 2) Fallback: cards visibles, evitando depender de hashes de clases.
    for card in soup.select('a[href^="/es/news/"], a[href^="/es/post/"]'):
        href = card.get("href")
        if not href:
            continue

        title = ""
        date_published = None
        image = None

        # El título visible suele ser un div con texto fuerte dentro de la card.
        candidate_divs = card.find_all("div")
        candidate_texts = [normalize_spaces(div.get_text(" ", strip=True)) for div in candidate_divs]
        candidate_texts = [t for t in candidate_texts if t]

        # Elegimos el primer texto que parezca el título del Community Day.
        for text in candidate_texts:
            if is_community_day_title(text):
                title = text
                break

        if not title:
            continue

        img = card.select_one("img")
        if img and img.has_attr("src"):
            image = img["src"]

        date_el = card.select_one("pg-date-format")
        if date_el:
            date_published = normalize_spaces(date_el.get_text(" ", strip=True))

        return {
            "title": title,
            "relative_url": href,
            "url": urljoin(BASE_URL, href),
            "image": image,
            "date_published": date_published,
        }

    return None


def build_structured_blocks(main: BeautifulSoup) -> list[dict]:
    blocks = []
    current_block = None

    for el in main.find_all(["h2", "h3", "p", "ul"], recursive=True):
        if el.name in ("h2", "h3"):
            title = normalize_spaces(el.get_text(" ", strip=True))
            if not title:
                continue

            if current_block and current_block["contenido"]:
                blocks.append(current_block)

            current_block = {
                "titulo": title,
                "contenido": [],
            }

        elif el.name == "p":
            text = normalize_spaces(el.get_text(" ", strip=True))
            if not text:
                continue

            if current_block is None:
                current_block = {
                    "titulo": "Introducción",
                    "contenido": [],
                }

            current_block["contenido"].append({
                "type": "paragraph",
                "text": text,
            })

        elif el.name == "ul":
            items = [
                normalize_spaces(li.get_text(" ", strip=True))
                for li in el.find_all("li")
            ]
            items = [item for item in items if item]

            if not items:
                continue

            if current_block is None:
                current_block = {
                    "titulo": "Introducción",
                    "contenido": [],
                }

            current_block["contenido"].append({
                "type": "list",
                "items": items,
            })

    if current_block and current_block["contenido"]:
        blocks.append(current_block)

    return blocks


def split_intro_and_sections(blocks: list[dict], intro_paragraph_limit: int = 4) -> tuple[list[dict], list[dict]]:
    intro = []
    sections = []
    paragraph_count = 0

    for block in blocks:
        intro_content = []
        section_content = []

        for item in block["contenido"]:
            if item["type"] == "paragraph" and paragraph_count < intro_paragraph_limit:
                intro_content.append(item)
                paragraph_count += 1
            else:
                section_content.append(item)

        if intro_content:
            intro.append({
                "titulo": block["titulo"],
                "contenido": intro_content,
            })

        if section_content:
            sections.append({
                "titulo": block["titulo"],
                "contenido": section_content,
            })

    return intro, sections


def scrape_article_detail(url: str) -> tuple[str, list[dict], list[dict]]:
    soup = fetch_html(url)
    main = soup.select_one("main")

    if not main:
        return "", [], []

    full_text = normalize_spaces(main.get_text("\n", strip=True))
    blocks = build_structured_blocks(main)
    intro, sections = split_intro_and_sections(blocks, intro_paragraph_limit=4)

    return full_text, sections, intro


def extract_exclusive_move_from_text(text: str) -> str:
    text_norm = normalize_spaces(text)

    patterns = [
        r"(?:podrá aprender|aprenderá|conocerá).*?(?:ataque cargado|ataque rápido)\s*[:：]?\s*(.+)$",
        r"(?:ataque destacado|movimiento exclusivo)\s*[:：]?\s*(.+)$",
        r"(?:ataque cargado|ataque rápido)\s*[:：]?\s*(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, text_norm, re.IGNORECASE)
        if match:
            return normalize_spaces(match.group(1))

    return ""


def extract_damage_from_list_item(line: str) -> tuple[str | None, int | None]:
    line_norm = normalize_spaces(line)
    num_match = re.search(r"(\d+)", line_norm)
    if not num_match:
        return None, None

    damage = int(num_match.group(1))
    line_lower = line_norm.lower()

    if "entrenador" in line_lower:
        return "Combates de Entrenador", damage
    if "gimnasio" in line_lower or "incurs" in line_lower:
        return "Gimnasios e incursiones", damage

    return None, None


def infer_pokemon_from_title(title: str) -> str:
    title = normalize_spaces(title)

    # Casos con ":" -> "Día de la Comunidad de mayo de 2026: Lechonk"
    if ":" in title:
        return normalize_spaces(title.split(":")[-1])

    # Casos como "Día de la Comunidad de abril de Tinkatink"
    match = re.search(r"día de la comunidad.*?de\s+([A-Za-zÀ-ÿ0-9' -]+)$", title, re.IGNORECASE)
    if match:
        return normalize_spaces(match.group(1))

    return ""


def extract_data(title: str, fulltext: str, blocks: list[dict], intro: list[dict]) -> dict:
    fecha_evento, hora_inicio, hora_fin = extract_event_datetime(fulltext)

    data = {
        "titulo": title,
        "pokemon": infer_pokemon_from_title(title),
        "fecha_evento": fecha_evento,
        "hora_inicio": hora_inicio,
        "hora_fin": hora_fin,
        "movimiento_exclusivo": "",
        "movimiento_dano": {},
        "intro": intro,
        "secciones": [],
    }

    for block in blocks:
        titulo = block["titulo"]
        contenido = block["contenido"]
        titulo_lower = titulo.lower()

        seccion = {
            "titulo": titulo,
            "contenido": contenido,
        }

        if (
            "ataque destacado" in titulo_lower
            or "movimiento" in titulo_lower
            or "ataque" in titulo_lower
        ):
            for elem in contenido:
                if elem["type"] == "paragraph" and not data["movimiento_exclusivo"]:
                    move = extract_exclusive_move_from_text(elem["text"])
                    if move:
                        data["movimiento_exclusivo"] = move

                elif elem["type"] == "list":
                    for line in elem["items"]:
                        key, damage = extract_damage_from_list_item(line)
                        if key and damage is not None:
                            data["movimiento_dano"][key] = damage

        data["secciones"].append(seccion)

    return data


def save_json(data: dict, metadata: dict) -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    result = {
        "metadata": metadata,
        "datos": data,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def main() -> None:
    article = get_community_day_article()

    if not article:
        print("❌ No se encontró noticia del Día de la Comunidad.")
        return

    url = article["url"]
    fulltext, blocks, intro = scrape_article_detail(url)
    parsed = extract_data(article["title"], fulltext, blocks, intro)

    metadata = {
        "url": url,
        "fecha_publicacion": article["date_published"],
        "imagen": article["image"],
    }

    save_json(parsed, metadata)
    print(f"✅ Archivo JSON generado con {parsed['titulo']}")


if __name__ == "__main__":
    main()