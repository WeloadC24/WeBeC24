#!/usr/bin/env python3

import os
import asyncio
import random
import time
import shutil
from datetime import datetime
from io import BytesIO

import requests
import google.generativeai as genai
from PIL import Image
from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

##################################
# HARTKODIERTER BOT-TOKEN / GEMINI-KEY
##################################
BOT_TOKEN = ""
api_key   = "AIzaSyD"

# Gemini initialisieren
genai.configure(api_key=api_key)
model = genai.GenerativeModel("gemini-1.5-flash")

##################################
# USER-AGENTS
##################################
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
]

##################################
# FUNKTION: Text rewriting via Gemini
##################################
def rewrite_description(text: str) -> str:
    if not text or not text.strip():
        return "Keine Beschreibung vorhanden."

    prompt = (
        "Übersetze den folgenden Text, der ursprünglich auf Niederländisch geschrieben ist, "
        "ins Deutsche und formuliere ihn in einem professionellen, klaren und leicht verständlichen Ton. "
        "Verbessere leicht den Stil, bleibe aber nah am Inhalt. Entferne bitte jegliche Straßennamen "
        "und deutliche Location-Hinweise. Schreibe sie in einem modernen, aber professionellen Stil.\n\n"
        f"{text}"
    )

    try:
        resp = model.generate_content(prompt)
        if not resp or not resp.text:
            return "Fehler: Leere Antwort vom Gemini-Model"
        return resp.text.strip()
    except Exception as e:
        return f"(Fehler bei Gemini: {e})\n\n{text}"

##################################
# FUNKTION: Cookie-Banner klicken
##################################
def akzeptiere_cookies(driver, timeout=10):
    try:
        WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.ID, "didomi-notice-agree-button"))
        )
        btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.ID, "didomi-notice-agree-button"))
        )
        btn.click()
        time.sleep(2)
    except TimeoutException:
        pass

##################################
# FUNKTION: Infos extrahieren
##################################
def extrahiere_info(soup):
    quadratmeter = None
    schlafzimmer = None
    beschreibung = None

    # Li-Parsing
    for li in soup.select('li.flex'):
        li_text = li.get_text(" ", strip=True).lower()
        bold_span = li.select_one('span.md\\:font-bold')
        if bold_span:
            val = bold_span.get_text(strip=True)
            if 'm²' in li_text:
                val = val.replace("m²", "").replace("m2","").strip()
                if val.isdigit():
                    quadratmeter = val
            elif 'slaapkamer' in li_text:
                if val.isdigit():
                    schlafzimmer = val

    # Beschreibung
    desc_element = soup.select_one('.listing-description-text')
    if desc_element:
        beschreibung = desc_element.get_text(strip=True)

    return quadratmeter, schlafzimmer, beschreibung

##################################
# FUNKTION: Thumbnails /overzicht
##################################
def extrahiere_overview_thumbnail_urls(driver):
    time.sleep(3)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    thumbs = soup.select("ul.mt-6 li a")
    links = []
    for t in thumbs:
        href = t.get("href", "")
        if "/media/foto/" in href:
            links.append(href)
    return links

##################################
# FUNKTION: HQ-Bild aus srcset
##################################
def extrahiere_hq_bild(driver):
    try:
        big_image = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "img[srcset]"))
        )
        srcset = big_image.get_attribute("srcset")
        if srcset:
            parts = [p.strip() for p in srcset.split(",")]
            last_part = parts[-1]
            return last_part.split()[0]
        else:
            return big_image.get_attribute("src")
    except:
        return None

##################################
# FUNKTION: Bilderdownload
##################################
def download_bilder(urls, folder, referer):
    os.makedirs(folder, exist_ok=True)
    for i, url in enumerate(urls, start=1):
        try:
            agent = random.choice(USER_AGENTS)
            headers = {"User-Agent": agent, "Referer": referer}
            resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()

            im = Image.open(BytesIO(resp.content))
            w, h = im.size
            nw = max(1, w + random.randint(-1, 1))
            nh = max(1, h + random.randint(-1, 1))
            im = im.resize((nw, nh))

            # Minimales Rauschen
            px = im.load()
            for x in range(nw):
                for y in range(nh):
                    rr, gg, bb = px[x,y]
                    noise = random.randint(-1,1)
                    px[x,y] = (
                        max(0,min(255, rr+noise)),
                        max(0,min(255, gg+noise)),
                        max(0,min(255, bb+noise))
                    )

            path = os.path.join(folder, f"foto_{i}.jpg")
            im.save(path)
        except Exception as e:
            print(f"[WARN] Fehler beim Download {url}: {e}")

##################################
# FUNKTION: Scraper
##################################
def funda_scrape(url: str) -> str:
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument("--incognito")

    driver = webdriver.Chrome(options=options)
    try:
        chosen_ua = random.choice(USER_AGENTS)
        driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": chosen_ua})

        driver.get(url)
        time.sleep(3)
        akzeptiere_cookies(driver)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        qm, sz, descr = extrahiere_info(soup)

        # /overzicht
        over = url.rstrip("/") + "/overzicht"
        driver.get(over)
        time.sleep(3)
        flinks = extrahiere_overview_thumbnail_urls(driver)

        hq_urls = []
        for link in flinks:
            if link.startswith("/"):
                link = "https://www.funda.nl" + link
            driver.get(link)
            time.sleep(random.uniform(2,4))
            found = extrahiere_hq_bild(driver)
            if found:
                hq_urls.append(found)

        # Ordner
        base_dir = os.path.join(os.getcwd(), "Objekte")
        os.makedirs(base_dir, exist_ok=True)
        now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if qm and sz:
            foldername = f"{qm}qm-{sz}Schlafzimmer-{now}"
        else:
            foldername = f"Unbekannt-{now}"

        fullp = os.path.join(base_dir, foldername)
        os.makedirs(fullp, exist_ok=True)

        download_bilder(hq_urls, fullp, over)

        # rewrite
        descr_de = rewrite_description(descr)
        info_path = os.path.join(fullp, "infos.txt")
        with open(info_path, "w", encoding="utf-8") as f:
            f.write(f"Quadratmeter: {qm}\n")
            f.write(f"Schlafzimmer: {sz}\n")
            f.write("Beschreibung (Original):\n")
            if descr:
                f.write(descr + "\n")
            else:
                f.write("Keine Beschreibung vorhanden.\n")

            f.write("\nBeschreibung (Deutsch, umgeschrieben):\n")
            f.write(descr_de + "\n")

        return fullp
    finally:
        driver.quit()

def zip_folder(folder_path: str) -> str:
    zip_path = folder_path + ".zip"
    shutil.make_archive(folder_path, "zip", root_dir=folder_path)
    return zip_path

##################################
# TELEGRAM
##################################
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hallo! Sende mir eine Funda-URL.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "funda.nl" in text.lower():
        await update.message.reply_text("Einen Moment, ich scrape jetzt...")
        folder = funda_scrape(text)
        zipf = zip_folder(folder)
        with open(zipf, "rb") as f:
            await update.message.reply_document(document=f, filename=os.path.basename(zipf))
    else:
        await update.message.reply_text("Bitte eine gültige Funda-URL angeben.")

async def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN leer!")
        return

    from telegram.ext import ApplicationBuilder
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT, handle_message))

    print("[INFO] Telegram-Bot startet. Drücke Strg+C zum Beenden.")
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
