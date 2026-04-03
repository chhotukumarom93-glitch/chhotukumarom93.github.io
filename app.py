import os
import uuid
import sqlite3
from flask import Flask, render_template, request, send_from_directory, redirect, url_for, flash
from PIL import Image
from werkzeug.utils import secure_filename
from rembg import remove
from fpdf import FPDF

app = Flask(__name__)
app.secret_key = "my_secret_key_123"

# -------------------------
# DATABASE SETUP
# -------------------------
DB_FILE = "site_data.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Visitor counter table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY,
            total_visits INTEGER DEFAULT 0
        )
    """)

    # Contact messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT,
            message TEXT
        )
    """)

    # Insert default counter row if not exists
    cursor.execute("SELECT * FROM visits WHERE id = 1")
    row = cursor.fetchone()
    if not row:
        cursor.execute("INSERT INTO visits (id, total_visits) VALUES (1, 0)")

    conn.commit()
    conn.close()

# Start DB on app run
init_db()

# -----------------------------
# FOLDERS
# -----------------------------
UPLOAD_FOLDER = "uploads"
STATIC_FOLDER = "static"
OUTPUT_FOLDER = os.path.join(STATIC_FOLDER, "outputs")
PREVIEW_FOLDER = os.path.join(STATIC_FOLDER, "previews")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(PREVIEW_FOLDER, exist_ok=True)

# -----------------------------
# HELPERS
# -----------------------------
def unique_filename(filename):
    ext = filename.split(".")[-1]
    return f"{uuid.uuid4().hex}.{ext}"

def save_image(img, output_path, fmt="JPEG", quality=90):
    if fmt.upper() == "JPG":
        fmt = "JPEG"

    if fmt.upper() == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    img.save(output_path, format=fmt, quality=quality, optimize=True)

def create_preview(img, filename):
    preview_path = os.path.join(PREVIEW_FOLDER, filename)
    preview_img = img.copy()
    preview_img.thumbnail((500, 500))
    if preview_img.mode in ("RGBA", "P"):
        preview_img.save(preview_path, format="PNG")
    else:
        preview_img.save(preview_path, format="JPEG", quality=85)
    return filename

def compress_to_target(img, output_path, target_kb=100, fmt="JPEG"):
    if fmt.upper() == "JPG":
        fmt = "JPEG"

    if fmt.upper() == "JPEG" and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    quality = 95
    while quality >= 10:
        img.save(output_path, format=fmt, quality=quality, optimize=True)
        size_kb = os.path.getsize(output_path) / 1024
        if size_kb <= target_kb:
            return True
        quality -= 5
    return False

def resize_exact(img, width, height):
    return img.resize((width, height))

# -----------------------------
# ROUTES
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def home():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # increase visit count
    cursor.execute("UPDATE visits SET total_visits = total_visits + 1 WHERE id = 1")
    conn.commit()

    # get updated count
    cursor.execute("SELECT total_visits FROM visits WHERE id = 1")
    visitor_count = cursor.fetchone()[0]

    conn.close()

    result = {}
    selected_tool = "compress"

    if request.method == "POST":
        tool = request.form.get("tool", "compress")
        selected_tool = tool

        try:
            # =========================
            # PDF TOOL
            # =========================
            if tool == "pdf":
                files = request.files.getlist("images")
                valid_files = [f for f in files if f and f.filename.strip() != ""]

                if not valid_files:
                    result["error"] = "Please upload images for PDF."
                    return render_template(
                        "index.html",
                        result=result,
                        selected_tool=selected_tool,
                        visitor_count=visitor_count
                    )

                pdf = FPDF()
                temp_paths = []

                for f in valid_files:
                    filename = unique_filename(secure_filename(f.filename))
                    temp_path = os.path.join(UPLOAD_FOLDER, filename)
                    f.save(temp_path)
                    temp_paths.append(temp_path)

                for img_path in temp_paths:
                    img = Image.open(img_path).convert("RGB")
                    temp_jpg = img_path + ".jpg"
                    img.save(temp_jpg, "JPEG")

                    pdf.add_page()
                    pdf.image(temp_jpg, x=10, y=10, w=190)

                    os.remove(temp_jpg)

                output_name = f"{uuid.uuid4().hex}.pdf"
                output_path = os.path.join(OUTPUT_FOLDER, output_name)
                pdf.output(output_path)

                result["success"] = "PDF created successfully!"
                result["download_file"] = output_name
                result["preview_file"] = None
                result["preview_folder"] = "outputs"

                return render_template(
                    "index.html",
                    result=result,
                    selected_tool=selected_tool,
                    visitor_count=visitor_count
                )

            # -----------------------------
            # SINGLE IMAGE TOOLS
            # -----------------------------
            file = request.files.get("image")

            if not file or file.filename.strip() == "":
                if tool == "bgremove":
                    result["error"] = "Please upload an image for background removal."
                else:
                    result["error"] = "Please upload an image."
                return render_template(
                    "index.html",
                    result=result,
                    selected_tool=selected_tool,
                    visitor_count=visitor_count
                )

            filename = unique_filename(secure_filename(file.filename))
            upload_path = os.path.join(UPLOAD_FOLDER, filename)
            file.save(upload_path)

            img = Image.open(upload_path)

            width = request.form.get("width", "").strip()
            height = request.form.get("height", "").strip()
            target_kb = request.form.get("target_kb", "").strip()
            output_format = request.form.get("format", "JPG").upper()

            width = int(width) if width.isdigit() else None
            height = int(height) if height.isdigit() else None
            target_kb = int(target_kb) if target_kb.isdigit() else 100

            ext_map = {
                "JPG": "jpg",
                "PNG": "png",
                "WEBP": "webp"
            }
            output_ext = ext_map.get(output_format, "jpg")
            output_name = f"{uuid.uuid4().hex}.{output_ext}"
            output_path = os.path.join(OUTPUT_FOLDER, output_name)

            # -----------------------------
            # IMAGE COMPRESSOR
            # -----------------------------
            if tool == "compress":
                working_img = img.copy()

                if width and height:
                    working_img = working_img.resize((width, height))
                elif width:
                    ratio = width / working_img.width
                    new_height = int(working_img.height * ratio)
                    working_img = working_img.resize((width, new_height))
                elif height:
                    ratio = height / working_img.height
                    new_width = int(working_img.width * ratio)
                    working_img = working_img.resize((new_width, height))

                compress_to_target(working_img, output_path, target_kb=target_kb, fmt=output_format)

                preview_name = f"preview_{output_name}"
                create_preview(working_img, preview_name)

                result["success"] = "Image compressed successfully!"
                result["download_file"] = output_name
                result["preview_file"] = preview_name
                result["preview_folder"] = "previews"

            # -----------------------------
            # PASSPORT PHOTO
            # -----------------------------
            elif tool == "passport":
                # Passport size approx: 413x531 px
                working_img = img.copy().convert("RGB")
                working_img = resize_exact(working_img, 413, 531)

                save_image(working_img, output_path, fmt=output_format, quality=95)

                preview_name = f"preview_{output_name}"
                create_preview(working_img, preview_name)

                result["success"] = "Passport photo created successfully!"
                result["download_file"] = output_name
                result["preview_file"] = preview_name
                result["preview_folder"] = "previews"

            # -----------------------------
            # SIGNATURE RESIZE
            # -----------------------------
            elif tool == "signature":
                # Signature approx: 300x100
                working_img = img.copy().convert("RGBA")
                working_img = resize_exact(working_img, 300, 100)

                if output_format == "JPG":
                    working_img = working_img.convert("RGB")

                save_image(working_img, output_path, fmt=output_format, quality=95)

                preview_name = f"preview_{output_name}"
                create_preview(working_img, preview_name)

                result["success"] = "Signature resized successfully!"
                result["download_file"] = output_name
                result["preview_file"] = preview_name
                result["preview_folder"] = "previews"

            # -----------------------------
            # FORM PHOTO
            # -----------------------------
            elif tool == "formphoto":
                # Common form photo approx: 300x400
                working_img = img.copy().convert("RGB")
                working_img = resize_exact(working_img, 300, 400)

                save_image(working_img, output_path, fmt=output_format, quality=95)

                preview_name = f"preview_{output_name}"
                create_preview(working_img, preview_name)

                result["success"] = "Form photo resized successfully!"
                result["download_file"] = output_name
                result["preview_file"] = preview_name
                result["preview_folder"] = "previews"

            # -----------------------------
            # BACKGROUND REMOVER
            # -----------------------------
            elif tool == "bgremove":
                input_image = Image.open(upload_path).convert("RGBA")
                output_image = remove(input_image)

                output_name = f"{uuid.uuid4().hex}.png"
                output_path = os.path.join(OUTPUT_FOLDER, output_name)
                output_image.save(output_path)

                preview_name = f"preview_{output_name}"
                create_preview(output_image, preview_name)

                result["success"] = "Background removed successfully!"
                result["download_file"] = output_name
                result["preview_file"] = preview_name
                result["preview_folder"] = "previews"

            # -----------------------------
            # THUMBNAIL TOOL
            # -----------------------------
            elif tool == "thumbnail":
                # YouTube thumbnail approx: 1280x720
                working_img = img.copy().convert("RGB")
                working_img = resize_exact(working_img, 1280, 720)

                save_image(working_img, output_path, fmt=output_format, quality=95)

                preview_name = f"preview_{output_name}"
                create_preview(working_img, preview_name)

                result["success"] = "Thumbnail created successfully!"
                result["download_file"] = output_name
                result["preview_file"] = preview_name
                result["preview_folder"] = "previews"

            else:
                result["error"] = "Invalid tool selected."

        except Exception as e:
            result["error"] = f"Error: {str(e)}"

    return render_template("index.html", result=result, selected_tool=selected_tool, visitor_count=visitor_count)

# -----------------------------
# DOWNLOAD ROUTE
# -----------------------------

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    success = None

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        message = request.form.get("message")

        if name and email and message:
            with open("messages.txt", "a", encoding="utf-8") as f:
                f.write("===== New Message =====\n")
                f.write(f"Name: {name}\n")
                f.write(f"Email: {email}\n")
                f.write(f"Message: {message}\n")
                f.write("=======================\n\n")

            success = "Your message has been sent successfully!"
        else:
            success = "Please fill all fields."

    return render_template("contact.html", success=success)

@app.route("/download/<folder>/<filename>")
def download_file(folder, filename):
    folder_path = os.path.join("static", folder)
    return send_from_directory(folder_path, filename, as_attachment=True)

# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)