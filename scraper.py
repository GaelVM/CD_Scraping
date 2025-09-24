# scraper.py
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
import os
import re

BASE_URL = "https://pokemongo.com"
NEWS_URL = f"{BASE_URL}/es/news"
OUTPUT_PATH = "data/community_day.json"

headers = {
    "User-Agent": "Mozilla/5.0 (compatible; GitHubActionBot/1.0)"
}

def get_community_day_article():
    res = requests.get(NEWS_URL, headers=headers)
    soup = BeautifulSoup(res.content, "lxml")
    news_cards = soup.select("a._newsCard_119ao_16")

    for card in news_cards:
        title = card.select_one("div._size\\:heading_sfz9t_19")
        if not title:
            continue
        if "Día de la Comunidad" in title.text:
            return {
                "title": title.text.strip(),
                "relative_url": card["href"],
                "image": card.select_one("img")["src"],
                "date_published": card.select_one("pg-date-format").text.strip()
            }
    return None

def scrape_article_detail(url):
    res = requests.get(url, headers=headers)
    soup = BeautifulSoup(res.content, "lxml")

    blocks = []
    main_intro = []
    intro_limit_reached = False
    paragraph_count = 0

    for block in soup.select("main ._containerBlock_1vtb5_2"):
        heading = block.select_one("h2")
        body = []
        if heading:
            for el in block.select("._markdown_sfz9t_273 > *"):
                if el.name == "p":
                    text = el.get_text(strip=True)
                    body.append({"type": "paragraph", "text": text})
                    paragraph_count += 1
                    if paragraph_count >= 4:  # limitar introducción a los 4 primeros párrafos
                        intro_limit_reached = True
                        break
                elif el.name == "ul":
                    items = [li.get_text(strip=True) for li in el.find_all("li")]
                    body.append({"type": "list", "items": items})
        if heading and body:
            if not intro_limit_reached:
                main_intro.append({"titulo": heading.get_text(strip=True), "contenido": body})
            else:
                blocks.append({"titulo": heading.get_text(strip=True), "contenido": body})

    full_text = "\n".join([tag.get_text(separator="\n") for tag in soup.select("main")])
    return full_text, blocks, main_intro

def extract_data(title, fulltext, blocks, intro):
    data = {
        "titulo": title,
        "pokemon": title.split(":")[-1].strip() if ":" in title else "",
        "fecha_evento": "",
        "hora_inicio": "",
        "hora_fin": "",
        "movimiento_exclusivo": "",
        "movimiento_dano": {},
        "intro": intro,
        "secciones": []
    }

    match = re.search(r"(\d{1,2} de .*? de \d{4}).*?(\d{1,2}:\d{2}).*?(\d{1,2}:\d{2})", fulltext)
    if match:
        fecha_raw, hora_inicio, hora_fin = match.groups()
        try:
            fecha_formateada = datetime.strptime(fecha_raw, "%d de %B de %Y").strftime("%Y-%m-%d")
        except:
            fecha_formateada = fecha_raw
        data["fecha_evento"] = fecha_formateada
        data["hora_inicio"] = hora_inicio
        data["hora_fin"] = hora_fin

    for block in blocks:
        titulo = block["titulo"].lower()
        contenido = block["contenido"]
        seccion = {"titulo": block["titulo"], "contenido": contenido}

        if "ataque destacado" in titulo:
            for elem in contenido:
                if elem["type"] == "paragraph":
                    match = re.search(r"ataque cargado (.+?)$", elem["text"], re.IGNORECASE)
                    if match:
                        data["movimiento_exclusivo"] = match.group(1)
                elif elem["type"] == "list":
                    for line in elem["items"]:
                        if "Entrenador" in line:
                            data["movimiento_dano"]["Combates de Entrenador"] = int(re.search(r"(\d+)", line).group(1))
                        elif "Gimnasios" in line:
                            data["movimiento_dano"]["Gimnasios e incursiones"] = int(re.search(r"(\d+)", line).group(1))

        data["secciones"].append(seccion)

    return data

def save_json(data, metadata):
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    result = {"metadata": metadata, "datos": data}
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    article = get_community_day_article()
    if article:
        url = BASE_URL + article["relative_url"]
        fulltext, blocks, intro = scrape_article_detail(url)
        parsed = extract_data(article["title"], fulltext, blocks, intro)

        metadata = {
            "url": url,
            "fecha_publicacion": article["date_published"],
            "imagen": article["image"]
        }

        save_json(parsed, metadata)
        print("✅ Archivo JSON generado con ", parsed["titulo"])
    else:
        print("❌ No se encontró noticia del Día de la Comunidad.")
