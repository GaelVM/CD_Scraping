import json
import os
import re
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://pokemongo.com"
NEWS_URL = f"{BASE_URL}/es/news"
OUTPUT_PATH = "data/community_days.json"

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
    return re.sub(r"\s+", " ", text or "").strip()


def get_community_day_type(title: str) -> str | None:
    title_norm = normalize_spaces(title).lower()

    if "día de la comunidad" not in title_norm:
        return None

    if "clásico" in title_norm or "clasico" in title_norm:
        return "clasico"

    return "normal"


def parse_spanish_date(fecha_raw: str) -> str:
    text = normalize_spaces(fecha_raw).lower()
    match = re.search(r"(\d{1,2}) de ([a-záéíóú]+) de (\d{4})", text)

    if not match:
        return fecha_raw

    day, month_name, year = match.groups()
    month = SPANISH_MONTHS.get(month_name)

    if not month:
        return fecha_raw

    return f"{year}-{month}-{int(day):02d}"


def extract_event_datetime(fulltext: str) -> tuple[str, str, str]:
    text = normalize_spaces(fulltext)

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


def get_community_day_articles() -> list[dict]:
    soup = fetch_html(NEWS_URL)
    articles = []
    seen_urls = set()

    for block in get_json_ld_blocks(soup):
        if block.get("@type") != "ItemList":
            continue

        for item in block.get("itemListElement", []):
            if not isinstance(item, dict):
                continue

            title = normalize_spaces(item.get("name", ""))
            url = item.get("url", "")
            event_type = get_community_day_type(title)

            if event_type and url:
                full_url = urljoin(BASE_URL, url)

                if full_url in seen_urls:
                    continue

                seen_urls.add(full_url)

                articles.append({
                    "title": title,
                    "type": event_type,
                    "relative_url": url.replace(BASE_URL, "") if url.startswith(BASE_URL) else url,
                    "url": full_url,
                    "image": None,
                    "date_published": None,
                })

    for card in soup.select('a[href^="/es/news/"], a[href^="/es/post/"], a[href^="/news/"], a[href^="/post/"]'):
        href = card.get("href")

        if not href:
            continue

        full_url = urljoin(BASE_URL, href)

        if full_url in seen_urls:
            continue

        title = ""
        event_type = None
        image = None
        date_published = None

        candidate_texts = [
            normalize_spaces(div.get_text(" ", strip=True))
            for div in card.find_all("div")
        ]
        candidate_texts = [text for text in candidate_texts if text]

        for text in candidate_texts:
            detected_type = get_community_day_type(text)

            if detected_type:
                title = text
                event_type = detected_type
                break

        if not title or not event_type:
            continue

        img = card.select_one("img")
        if img and img.has_attr("src"):
            image = img["src"]

        date_el = card.select_one("pg-date-format")
        if date_el:
            date_published = normalize_spaces(date_el.get_text(" ", strip=True))

        seen_urls.add(full_url)

        articles.append({
            "title": title,
            "type": event_type,
            "relative_url": href,
            "url": full_url,
            "image": image,
            "date_published": date_published,
        })

    return articles


def get_article_metadata(soup: BeautifulSoup) -> dict:
    metadata = {
        "headline": None,
        "url": None,
        "image": None,
        "date_published": None,
        "date_modified": None,
    }

    for block in get_json_ld_blocks(soup):
        if block.get("@type") == "NewsArticle":
            metadata["headline"] = block.get("headline")
            metadata["url"] = block.get("url")
            metadata["image"] = block.get("image")
            metadata["date_published"] = block.get("datePublished")
            metadata["date_modified"] = block.get("dateModified")
            break

    return metadata


def build_structured_blocks(main: BeautifulSoup) -> list[dict]:
    blocks = []
    current_block = None

    article = main.select_one("article") or main

    for el in article.find_all(["h2", "h3", "p", "ul"], recursive=True):
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


def scrape_article_detail(url: str) -> tuple[dict, str, list[dict], list[dict]]:
    soup = fetch_html(url)
    main = soup.select_one("main")

    metadata = get_article_metadata(soup)

    if not main:
        return metadata, "", [], []

    full_text = normalize_spaces(main.get_text("\n", strip=True))
    blocks = build_structured_blocks(main)
    intro, sections = split_intro_and_sections(blocks)

    return metadata, full_text, sections, intro


def infer_pokemon_from_title(title: str) -> str:
    title = normalize_spaces(title)

    if ":" in title:
        return normalize_spaces(title.split(":")[-1])

    match = re.search(
        r"día de la comunidad(?: clásico)?(?: de)?(?: [a-záéíóú]+)?(?: de \d{4})?:?\s*([A-Za-zÀ-ÿ0-9' -]+)$",
        title,
        re.IGNORECASE,
    )

    if match:
        return normalize_spaces(match.group(1))

    return ""


def extract_exclusive_move_from_blocks(blocks: list[dict]) -> str:
    for block in blocks:
        title = block["titulo"].lower()

        if "ataque destacado" not in title and "movimiento" not in title:
            continue

        paragraphs = [
            item["text"]
            for item in block["contenido"]
            if item["type"] == "paragraph"
        ]

        for text in paragraphs:
            match = re.search(
                r"conozca el ataque (?:rápido|cargado)\s+(.+?)(?:\.|$)",
                text,
                re.IGNORECASE,
            )

            if match:
                return normalize_spaces(match.group(1))

            match = re.search(
                r"ataque (?:rápido|cargado)\s*[:：]?\s*(.+?)(?:\.|$)",
                text,
                re.IGNORECASE,
            )

            if match:
                return normalize_spaces(match.group(1))

        if len(paragraphs) >= 2:
            return paragraphs[1]

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


def extract_data(article: dict, fulltext: str, blocks: list[dict], intro: list[dict]) -> dict:
    fecha_evento, hora_inicio, hora_fin = extract_event_datetime(fulltext)

    data = {
        "titulo": article["title"],
        "tipo": article["type"],
        "pokemon": infer_pokemon_from_title(article["title"]),
        "fecha_evento": fecha_evento,
        "hora_inicio": hora_inicio,
        "hora_fin": hora_fin,
        "movimiento_exclusivo": extract_exclusive_move_from_blocks(blocks),
        "movimiento_dano": {},
        "intro": intro,
        "secciones": blocks,
    }

    for block in blocks:
        title = block["titulo"].lower()

        if "ataque destacado" not in title and "movimiento" not in title:
            continue

        for elem in block["contenido"]:
            if elem["type"] != "list":
                continue

            for line in elem["items"]:
                key, damage = extract_damage_from_list_item(line)

                if key and damage is not None:
                    data["movimiento_dano"][key] = damage

    return data


def load_existing_events() -> list[dict]:
    if not os.path.exists(OUTPUT_PATH):
        return []

    with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    return data if isinstance(data, list) else []


def save_or_update_events(new_events: list[dict]) -> None:
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    events = load_existing_events()

    index_by_url = {
        event.get("metadata", {}).get("url"): i
        for i, event in enumerate(events)
        if event.get("metadata", {}).get("url")
    }

    for event in new_events:
        url = event["metadata"]["url"]

        if url in index_by_url:
            events[index_by_url[url]] = event
        else:
            events.append(event)

    events.sort(
        key=lambda x: x.get("datos", {}).get("fecha_evento", ""),
        reverse=True,
    )

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)


def main() -> None:
    articles = get_community_day_articles()

    if not articles:
        print("❌ No se encontraron noticias del Día de la Comunidad.")
        return

    parsed_events = []

    for article in articles:
        try:
            metadata, fulltext, blocks, intro = scrape_article_detail(article["url"])
            parsed = extract_data(article, fulltext, blocks, intro)

            event = {
                "metadata": {
                    "url": metadata.get("url") or article["url"],
                    "relative_url": article["relative_url"],
                    "fecha_publicacion": metadata.get("date_published") or article["date_published"],
                    "fecha_modificacion": metadata.get("date_modified"),
                    "imagen": metadata.get("image") or article["image"],
                    "tipo": article["type"],
                },
                "datos": parsed,
            }

            parsed_events.append(event)
            print(f"✅ Procesado: {parsed['tipo']} - {parsed['titulo']}")

        except Exception as exc:
            print(f"⚠️ Error procesando {article['url']}: {exc}")

    save_or_update_events(parsed_events)

    print(f"✅ JSON actualizado: {OUTPUT_PATH}")
    print(f"✅ Eventos procesados: {len(parsed_events)}")


if __name__ == "__main__":
    main()