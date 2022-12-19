import os
import sys
from flask import Flask, render_template, flash, request, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import null
from sqlalchemy.sql import func
from datetime import datetime
from werkzeug.utils import secure_filename
from flask_session.__init__ import Session
import sqlite3
import atexit
import random
from PIL import ImageFile

from apscheduler.schedulers.background import BackgroundScheduler


UPLOAD_FOLDER = './static/images_added_by _the_user/'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}
SESSION_TYPE = 'memcache'

global memcache
memcache = {}

app = Flask(__name__, static_url_path='/static')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///keys.db'
db = SQLAlchemy(app)
sess = Session()

class Keys(db.Model):
    key_id = db.Column(db.String(200), primary_key=True)
    img_path = db.Column(db.String(200), nullable=False)
    date_created = db.Column(db.DateTime(timezone=True), server_default=func.now())
    date_updated = db.Column(db.DateTime(timezone=True), onupdate=func.now())

class MemcacheConfig(db.Model):
    capacity_MB = db.Column(db.Integer(), primary_key=True)
    replace_policy = db.Column(db.String(200))
    items_num = db.Column(db.Integer())
    items_size = db.Column(db.Integer())
    request_num = db.Column(db.Integer())
    hit_rate_percent = db.Column(db.Float())
    miss_rate_percent = db.Column(db.Float())

#Clear memcache_config table
with app.app_context():
    my_conn = sqlite3.connect('./instance/keys.db')
    my_conn.execute("DROP table IF EXISTS memcache_config")
    db.create_all()
    my_conn.execute('INSERT INTO memcache_config VALUES (?, ?, ?, ?, ?, ?, ?)', (5000000, "Random", 0, 0, 0, 0, 0)) #Default values for memcache_config
    my_conn.commit()
    my_conn.close()

#Update memconfig every 5 seconds
item_size_in_mem = 0 #Is updated whenever we add or remove file
request_num_from_mem = 0 #Is updated whenever we search
hit_rate_percent_from_mem = 0 #Is updated whenever we find our serach in memcache
miss_rate_percent_from_mem = 0 #Is updated whenever we don't our serach in memcache
def update_mem_config():
    with app.app_context():
        raw = MemcacheConfig.query.all()[0]
        raw.items_num = len(memcache)
        raw.items_size = item_size_in_mem
        raw.request_num = request_num_from_mem
        if request_num_from_mem > 0:
            raw.hit_rate_percent = (hit_rate_percent_from_mem/request_num_from_mem) * 100
            raw.miss_rate_percent = (miss_rate_percent_from_mem/request_num_from_mem) * 100
        db.session.commit()
        print("Memcache configs is updated", memcache)


scheduler = BackgroundScheduler()
scheduler.add_job(func=update_mem_config, trigger="interval", seconds=5)
scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())

#Functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    conn = sqlite3.connect('./instance/keys.db')
    conn.row_factory = sqlite3.Row
    return conn

def get_mem_db_connection():
    conn = sqlite3.connect('./instance/memcache_config.db')
    conn.row_factory = sqlite3.Row
    return conn

#Memcache operations
def put_in_memcache(key, value, img_size):
    mem_config = MemcacheConfig.query.all()[0]
    if (mem_config.items_size + img_size) > mem_config.capacity_MB:
        if(mem_config.replace_policy == "Random"):
            keyid, photo = random.choice(list(memcache.items()))
            invalidateKey(keyid, os.stat(mem_cache[key_id]).st_size)
        else:
            lru_key = list(memcache.keys())[0]
            invalidateKey(lru_key, os.stat(memcache[lru_key]).st_size)
    memcache[key] = value
    update_item_size(img_size, True)
    

def get_from_memcache(key):
    global request_num_from_mem 
    request_num_from_mem = request_num_from_mem + 1
    return memcache.get(key)

def clear_memcache():
    memcache.clear()

def invalidateKey(key, img_size):
    del memcache[key]
    update_item_size(img_size, False)

def update_item_size(img_size, isAdding):
    global item_size_in_mem
    if isAdding:
        item_size_in_mem = item_size_in_mem + img_size
    else:
        item_size_in_mem = item_size_in_mem - img_size

#Routes
@app.route('/')
def main():
    return render_template('main.html')

@app.route('/SearchanImage')
def SearchanImage():
    return render_template('SearchanImage.html')

@app.route('/memory_Cache')
def memory_Cache():
    raw = MemcacheConfig.query.all()[0] #We only have one raw
    capacity_MB = raw.capacity_MB
    replace_policy = raw.replace_policy
    items_num = raw.items_num
    items_size = raw.items_size
    request_num = raw.request_num
    hit_rate_percent = raw.hit_rate_percent
    miss_rate_percent = raw.miss_rate_percent

    return render_template('memory_Cache.html', capacity_MB = (capacity_MB/1000000), replace_policy = replace_policy,
                                                items_num = items_num, items_size = (items_size/1000000), request_num = request_num,
                                                hit_rate_percent = hit_rate_percent, miss_rate_percent = miss_rate_percent)

@app.route('/policy')
def policy():
    return render_template('policy.html')

@app.route('/saveImgLFS', methods=['GET', 'POST'])
def upload_file():
    if request.method == 'POST':
        file = request.files['image']
        key_id = request.form.get('img_key').strip()
        conn = get_db_connection()


        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            img_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(img_path)
            img_size = file.tell()

            raw = Keys.query.filter_by(key_id=key_id).first()
            key_exists = raw is not None
            if key_exists:
                raw.img_path = img_path #update in Database
                db.session.commit()
                if get_from_memcache(key_id):
                    invalidateKey(key_id, img_size)
                put_in_memcache(key_id, img_path, img_size)
                flash("Key Updated Successfully!")
            else: 
                #Save key and img_path into db                
                if key_id == null or key_id == '':
                    flash("Please enter a key for the photo")
                else:
                    conn.execute('INSERT INTO keys (key_id, img_path) VALUES (?, ?)', (key_id, img_path))
                    put_in_memcache(key_id, img_path, img_size)
                    flash("Key Added Successfully!")
        else:
            flash("Please choose a photo that is \'png\', \'jpg\' or \'jpeg\'")

        conn.commit()
        conn.close()
        return render_template('main.html')

@app.route('/saveConfig', methods=['GET', 'POST'])
def UploadDateToMem():
    if request.method == 'POST':
        capacity = request.form.get('myRange')
        replace_policy = request.form.get('format')
        mem_config = MemcacheConfig.query.all()[0]
        mem_config.capacity_MB = int(capacity) * 1000000
        mem_config.replace_policy = replace_policy
        db.session.commit()
        flash("Configs Added Successfully!")
    else:
        flash("Error Added !")
    return redirect("memory_Cache")

@app.route('/search', methods=['GET', 'POST'])
def search():
    key_id = request.form.get('img_key')
    #search in mem_cache
    img_path_from_memcache = get_from_memcache(key_id)
    if img_path_from_memcache:
        global hit_rate_percent_from_mem
        hit_rate_percent_from_mem = hit_rate_percent_from_mem + 1
        img_size = os.stat(memcache[key_id]).st_size
        invalidateKey(key_id, img_size)
        put_in_memcache(key_id, img_path_from_memcache, img_size)
        return render_template('SearchanImage.html', user_image = img_path_from_memcache)
    #Get from database  
    else:
        global miss_rate_percent_from_mem
        miss_rate_percent_from_mem = miss_rate_percent_from_mem + 1
        img_path = Keys.query.filter_by(key_id=key_id).first()
        if img_path:
            return render_template('SearchanImage.html', user_image = img_path.img_path)
        else:
            flash("Key is not found")
            return render_template('SearchanImage.html')


@app.route('/displayAllKeys' , methods=['GET', 'POST'])
def getAllKey():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        sqlite_select_query = """SELECT key_id from Keys"""
        cur.execute(sqlite_select_query)
        records = cur.fetchall()
        records = list(*zip(*records))
        conn.commit()
        print("Printing each row in column key")
        for column in records:
            print(column)
        conn.close()
        return render_template('displayAllKeys.html', keys_list = records)
    except sqlite3.Error as error:
        print("Failed to read data from sqlite table", error)
        return render_template('displayAllKeys.html')
    finally:
        if conn:
            conn.close()
            print("The SQLite connection is closed")

@app.route('/clear', methods=['POST'])
def clear():
    clear_memcache()
    return render_template('policy.html')


# Displays any errors
if __name__ == "__main__":
    app.secret_key = 'super secret key'
    app.config['SESSION_TYPE'] = 'filesystem'

    sess.init_app(app)
    app.run(debug=False)
