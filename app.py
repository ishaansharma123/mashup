# app.py
import os
import sys
import yt_dlp
from concurrent.futures import ThreadPoolExecutor, as_completed
from moviepy.editor import VideoFileClip
from pydub import AudioSegment
import logging
from flask import Flask, render_template, request, Response, stream_with_context, send_file, jsonify
import queue
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import shutil
from datetime import datetime

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Global queue for log messages
log_queue = queue.Queue()

# Email configuration
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = "your_email@gmail.com"  # Replace with your email
SMTP_PASSWORD = "your_app_password"      # Replace with your app password

def send_email_with_attachment(receiver_email, subject, body, attachment_path):
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USERNAME
        msg['To'] = receiver_email
        msg['Subject'] = subject

        msg.attach(MIMEText(body, 'plain'))

        with open(attachment_path, 'rb') as f:
            attachment = MIMEApplication(f.read(), _subtype='mp3')
            attachment.add_header('Content-Disposition', 'attachment', filename=os.path.basename(attachment_path))
            msg.attach(attachment)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

        log_queue.put(f"Email sent successfully to {receiver_email}")
        return True
    except Exception as e:
        log_queue.put(f"Error sending email: {str(e)}")
        return False

def cleanup_files(session_id):
    """Clean up all temporary files for a session"""
    folders = [
        os.path.join(os.getcwd(), f"1.links_{session_id}"),
        os.path.join(os.getcwd(), f"2.videos_{session_id}"),
        os.path.join(os.getcwd(), f"3.audios_{session_id}"),
        os.path.join(os.getcwd(), f"4.mashup_{session_id}")
    ]
    
    for folder in folders:
        if os.path.exists(folder):
            try:
                shutil.rmtree(folder)
                log_queue.put(f"Cleaned up folder: {folder}")
            except Exception as e:
                log_queue.put(f"Error cleaning up {folder}: {str(e)}")

def search_youtube_music_links(query, max_results):
    log_queue.put(f"FINDING LINKS for '{query}'")
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'force_generic_extractor': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            search_url = f"ytsearch{max_results}:{query}"
            result = ydl.extract_info(search_url, download=False)
        except Exception as e:
            log_queue.put(f"Error extracting info from YouTube: {e}")
            return []

    links = []
    for entry in result['entries']:
        try:
            link = f"https://www.youtube.com/watch?v={entry['id']}"
            links.append(link)
        except yt_dlp.utils.DownloadError as e:
            log_queue.put(f"Skipping {entry['title']}: {e}")

    log_queue.put(f"Found {len(links)} links")
    return links

def write_links_to_file(links, folder_path, file_name):
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    file_path = os.path.join(folder_path, file_name)

    if os.path.exists(file_path):
        os.remove(file_path)

    with open(file_path, 'w') as file:
        for link in links:
            file.write(f"{link}\n")

    if os.stat(file_path).st_size == 0:
        log_queue.put("No links were generated, file is empty!")
        raise ValueError("No links were generated, file is empty!")

def download_single_video(url, index, download_path):
    log_queue.put(f"DOWNLOADING video {index} from {url}")
    ydl_opts = {
        'format': 'bestvideo[height<=480]+bestaudio/best',
        'outtmpl': f'{download_path}/video_{index}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        downloaded_files = [f for f in os.listdir(download_path) if f.startswith(f"video_{index}.")]
        if downloaded_files:
            log_queue.put(f"Downloaded video {index} successfully")
            return os.path.join(download_path, downloaded_files[0])
        else:
            log_queue.put(f"Downloaded video file not found for {url}")
            return None
    except Exception as e:
        log_queue.put(f"Error downloading video {index}: {e}")
        return None

def download_all_videos(video_urls, download_path):
    downloaded_files = []
    with ThreadPoolExecutor() as executor:
        futures = {
            executor.submit(download_single_video, url, index, download_path): index
            for index, url in enumerate(video_urls, start=1)
        }

        for future in as_completed(futures):
            try:
                video_file = future.result()
                if video_file:
                    downloaded_files.append(video_file)
            except Exception as e:
                log_queue.put(f"Error occurred: {e}")

    return downloaded_files

def convert_all_videos_to_audio(video_files, audio_folder):
    log_queue.put("Converting videos to audio")
    if not os.path.exists(audio_folder):
        os.makedirs(audio_folder)

    for index, video_file in enumerate(video_files, start=1):
        try:
            video = VideoFileClip(video_file)
            audio_file = os.path.join(audio_folder, f'song_{index}.mp3')
            video.audio.write_audiofile(audio_file, codec='mp3', bitrate='192k', ffmpeg_params=["-loglevel", "quiet"])
            video.close()
            log_queue.put(f"Converted {video_file} to {audio_file}")
        except Exception as e:
            log_queue.put(f"Error converting {video_file} to audio: {e}")

def download_audio_from_links(links_folder, file_name, session_id):
    file_path = os.path.join(links_folder, file_name)
    if not os.path.exists(file_path):
        log_queue.put("Links file does not exist.")
        return

    with open(file_path, 'r') as file:
        links = file.readlines()

    video_folder = os.path.join(os.getcwd(), f"2.videos_{session_id}")
    os.makedirs(video_folder, exist_ok=True)
    
    downloaded_videos = download_all_videos([link.strip() for link in links if link.strip()], video_folder)

    if downloaded_videos:
        log_queue.put(f"Downloaded {len(downloaded_videos)} video files to {video_folder}.")
        audio_folder = os.path.join(os.getcwd(), f"3.audios_{session_id}")
        convert_all_videos_to_audio(downloaded_videos, audio_folder)
    else:
        log_queue.put("No video files were downloaded.")

def create_mashup(input_dir, output_file, duration):
    log_queue.put("GENERATING Mashup file")
    mashup = AudioSegment.silent(duration=0)
    
    for filename in os.listdir(input_dir):
        if filename.endswith('.mp3') or filename.endswith('.wav') or filename.endswith('.ogg'):
            audio_path = os.path.join(input_dir, filename)
            audio = AudioSegment.from_file(audio_path)
            
            if len(audio) > duration * 1000:  # Convert seconds to milliseconds
                audio = audio[:duration * 1000]
            else:
                audio += AudioSegment.silent(duration=(duration * 1000) - len(audio))
            
            mashup += audio
            log_queue.put(f'Added {filename} to the mashup')
    
    mashup_path = output_file
    os.makedirs(os.path.dirname(mashup_path), exist_ok=True)
    if os.path.exists(mashup_path):
        os.remove(mashup_path)
    mashup.export(mashup_path, format='mp3')
    log_queue.put(f'Mashup saved as {mashup_path}')
    return mashup_path

def create_mashup_process(singer_name, number_of_videos, duration, email):
    try:
        # Create a unique session ID
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_queue.put("Starting mashup creation process...")

        # Create session-specific folders
        folder_path = os.path.join(os.getcwd(), f"1.links_{session_id}")
        mashup_folder = os.path.join(os.getcwd(), f"4.mashup_{session_id}")
        os.makedirs(folder_path, exist_ok=True)
        os.makedirs(mashup_folder, exist_ok=True)

        file_name = "links.txt"
        links = search_youtube_music_links(f"{singer_name} official new video song", number_of_videos)

        if not links:
            log_queue.put("No links found for the query.")
            cleanup_files(session_id)
            return False

        write_links_to_file(links, folder_path, file_name)
        download_audio_from_links(folder_path, file_name, session_id)

        audio_folder = os.path.join(os.getcwd(), f"3.audios_{session_id}")
        final_filename = f"{singer_name.replace(' ', '_')}_mashup.mp3"
        mashup_path = os.path.join(mashup_folder, final_filename)
        
        mashup_path = create_mashup(audio_folder, mashup_path, duration)

        # Send email with attachment
        email_subject = f"Your {singer_name} Mashup is Ready!"
        email_body = f"Hello,\n\nYour mashup has been created successfully. Please find it attached.\n\nEnjoy!"
        
        if send_email_with_attachment(email, email_subject, email_body, mashup_path):
            log_queue.put("Mashup sent via email successfully!")
        else:
            log_queue.put("Failed to send mashup via email")

        # Clean up files
        cleanup_files(session_id)
        log_queue.put("Cleanup completed")
        
        return True
    except Exception as e:
        log_queue.put(f"Error occurred: {str(e)}")
        cleanup_files(session_id)
        return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/create_mashup', methods=['POST'])
def create_mashup_endpoint():
    try:
        singer_name = request.form['singer_name']
        number_of_videos = int(request.form['num_videos'])
        duration = int(request.form['trim_duration'])
        email = request.form['receiver_email']
        
        if not (10 <= number_of_videos <= 30):
            return jsonify({'status': 'error', 'message': 'Number of videos must be between 10 and 30'})
        
        if not (20 <= duration <= 500):
            return jsonify({'status': 'error', 'message': 'Duration must be between 20 and 500 seconds'})
        
        thread = threading.Thread(
            target=create_mashup_process,
            args=(singer_name, number_of_videos, duration, email)
        )
        thread.start()
        
        return jsonify({
            'status': 'success',
            'message': 'Mashup creation process started. You will receive the mashup via email when it\'s ready.'
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error: {str(e)}'
        })

@app.route('/logs')
def logs():
    def generate():
        while True:
            try:
                log_message = log_queue.get()
                yield f'data: {{"type": "log", "message": "{log_message}"}}\n\n'
            except Exception as e:
                yield f'data: {{"type": "error", "message": "Error: {str(e)}"}}\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream'
    )

if __name__ == "__main__":
    app.run(debug=True)