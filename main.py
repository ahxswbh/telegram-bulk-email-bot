import os, csv, time, smtplib
from itertools import cycle
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from flask import Flask, request, send_file    # for tracker
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)

# ——— Load env —————————————————————————————————

BOT_TOKEN      = os.getenv("BOT_TOKEN")
ADMINS         = set(int(x) for x in os.getenv("ADMINS","").split(",") if x)
TRACKING_URL   = os.getenv("TRACKING_PIXEL_URL")
DEPLOYED_URL   = os.getenv("DEPLOYED_URL","")  # for webhook
# Gmail rotation
gmail_accounts = []
i=1
while True:
    e = os.getenv(f"EMAIL_{i}"); p = os.getenv(f"PASS_{i}")
    if not e or not p: break
    gmail_accounts.append({"email":e,"password":p}); i+=1

if not BOT_TOKEN or not gmail_accounts or not ADMINS or not TRACKING_URL:
    raise RuntimeError("❌ Missing required ENV vars!")

gmail_cycle = cycle(gmail_accounts)
RECIPIENTS_FILE   = "recipients.csv"
HTML_TEMPLATE_FILE= "templates/current.html"
ATTACH_DIR        = "attachments"
RATE_SECONDS      = int(os.getenv("RATE_SECONDS",5))

# ——— Email & CSV Helpers ———————————————————————

def load_template():
    if os.path.exists(HTML_TEMPLATE_FILE):
        return open(HTML_TEMPLATE_FILE,"r",encoding="utf-8").read()
    return open("templates/default.html","r",encoding="utf-8").read()

def validate_csv(path):
    req = {"name","email"}
    with open(path,newline="",encoding="utf-8") as f:
        hdrs = set(csv.DictReader(f).fieldnames or [])
    missing = req - hdrs
    if missing: raise ValueError(f"Missing columns: {', '.join(missing)}")

def send_email(name,to_email,attachments):
    sender = next(gmail_cycle)
    tpl    = load_template()
    pxl    = f"{TRACKING_URL}?email={to_email}"
    body   = tpl.replace("{name}",name).replace("{tracking_pixel}",pxl)
    msg = MIMEMultipart(); msg["From"]=sender["email"]; msg["To"]=to_email
    msg["Subject"]="🚀 CoinX Launch Invitation"
    msg.attach(MIMEText(body,"html"))
    for fn in attachments:
        fp = os.path.join(ATTACH_DIR,fn)
        if os.path.exists(fp):
            part=MIMEApplication(open(fp,"rb").read(),Name=fn)
            part["Content-Disposition"]=f'attachment; filename="{fn}"'
            msg.attach(part)
    s=smtplib.SMTP("smtp.gmail.com",587); s.starttls()
    s.login(sender["email"],sender["password"])
    s.sendmail(sender["email"],to_email,msg.as_string()); s.quit()

# ——— Telegram Handlers ———————————————————————

async def start(u:Update,ctx:ContextTypes.DEFAULT_TYPE):
    await u.message.reply_text(
        "✉️ *Bulk Email Bot*\n"
        "/upload  – upload CSV\n"
        "/template– upload HTML (.html)\n"
        "/attach  – upload attachments\n"
        "/preview – preview template\n"
        "/send    – send bulk emails\n"
        "/analytics– view opens\n",
        parse_mode="Markdown"
    )

async def upload(u,ctx):
    doc=u.message.document
    if not doc or not doc.file_name.endswith(".csv"):
        return await u.message.reply_text("❌ Send a .csv file")
    await doc.get_file().download_to_drive(RECIPIENTS_FILE)
    try:
        validate_csv(RECIPIENTS_FILE)
        await u.message.reply_text("✅ CSV uploaded & validated")
    except Exception as e:
        os.remove(RECIPIENTS_FILE)
        await u.message.reply_text(f"❌ CSV error: {e}")

async def template_cmd(u,ctx):
    if u.effective_user.id not in ADMINS:
        return await u.message.reply_text("❌ Not authorized")
    await u.message.reply_text("📤 Send .html file now")

async def handle_html(u,ctx):
    if u.effective_user.id not in ADMINS: return
    doc=u.message.document
    if not doc.file_name.endswith(".html"):
        return await u.message.reply_text("❌ Only .html allowed")
    os.makedirs("templates",exist_ok=True)
    await doc.get_file().download_to_drive(HTML_TEMPLATE_FILE)
    await u.message.reply_text("✅ Template updated")

async def preview(u,ctx):
    tpl = load_template().replace("{name}","Alice").replace("{tracking_pixel}","")
    await u.message.reply_text(tpl,parse_mode="HTML")

async def attach_cmd(u,ctx):
    if u.effective_user.id not in ADMINS:
        return await u.message.reply_text("❌ Not authorized")
    await u.message.reply_text("📤 Send file(s) to attach")

async def handle_attach(u,ctx):
    if u.effective_user.id not in ADMINS: return
    doc=u.message.document
    os.makedirs(ATTACH_DIR,exist_ok=True)
    await doc.get_file().download_to_drive(os.path.join(ATTACH_DIR,doc.file_name))
    await u.message.reply_text(f"✅ Attached: {doc.file_name}")

async def send_all(u,ctx):
    if not os.path.exists(RECIPIENTS_FILE):
        return await u.message.reply_text("❌ Upload CSV first")
    validate_csv(RECIPIENTS_FILE)
    atts = os.listdir(ATTACH_DIR) if os.path.isdir(ATTACH_DIR) else []
    total=succ=0
    for row in csv.DictReader(open(RECIPIENTS_FILE,encoding="utf-8")):
        total+=1
        try:
            send_email(row["name"],row["email"],atts)
            succ+=1
            await u.message.reply_text(f"✅ {row['email']}")
        except Exception as e:
            await u.message.reply_text(f"❌ {row['email']}: {e}")
        time.sleep(RATE_SECONDS)
    await u.message.reply_text(f"🎉 Done: {succ}/{total}")

async def analytics(u,ctx):
    if u.effective_user.id not in ADMINS:
        return await u.message.reply_text("❌ Not authorized")
    if not os.path.exists("analytics.log"):
        return await u.message.reply_text("ℹ️ No opens yet")
    text = open("analytics.log").read()[-1000:]
    await u.message.reply_text(f"📊 Opens:\n```\n{text}\n```",parse_mode="Markdown")

# ——— Webhook Setup —————————————————————————

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    # commands
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("upload",upload))
    app.add_handler(CommandHandler("template",template_cmd))
    app.add_handler(CommandHandler("preview",preview))
    app.add_handler(CommandHandler("attach",attach_cmd))
    app.add_handler(CommandHandler("send",send_all))
    app.add_handler(CommandHandler("analytics",analytics))
    # file handlers
    app.add_handler(MessageHandler(filters.Document.FileExtension("html"),handle_html))
    app.add_handler(MessageHandler(filters.Document.ALL),handle_attach)

    # webhook
    url = f"{DEPLOYED_URL}/{BOT_TOKEN}"
    app.run_webhook(listen="0.0.0.0",port=int(os.getenv("PORT",8443)),
                    url_path=BOT_TOKEN,webhook_url=url)

if __name__=="__main__":
    main()
