import os
import sqlite3
import json
import string
import random
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cantonese_therapy.db")

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    # 1. Users table - combined schema from all versions
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name TEXT,
        phone TEXT,
        role TEXT DEFAULT 'client',
        therapist_id INTEGER,
        therapist_code TEXT UNIQUE,
        avatar_url TEXT DEFAULT 'https://img.icons8.com/color/96/user-male-circle--v1.png',
        has_completed_analysis BOOLEAN DEFAULT 0,
        joined_date TEXT,
        current_streak INTEGER DEFAULT 0,
        last_practice_date TEXT,
        hide_guide_tip INTEGER DEFAULT 0,
        show_in_ranking INTEGER DEFAULT 1,
        preferred_language TEXT DEFAULT 'zh',
        FOREIGN KEY(therapist_id) REFERENCES users(id)
    )''')

    # 2. Analysis reports table - with dual reports and therapist comment
    c.execute('''CREATE TABLE IF NOT EXISTS analysis_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        report_content TEXT,
        professional_report TEXT,
        therapist_comment TEXT,
        raw_data TEXT,
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # 3. Mistakes table
    c.execute('''CREATE TABLE IF NOT EXISTS mistakes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        word TEXT,
        target_jyutping TEXT,
        user_jyutping TEXT,
        score INTEGER,
        created_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # 4. Practice sessions table
    c.execute('''CREATE TABLE IF NOT EXISTS practice_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        exercise_type TEXT,
        score INTEGER,
        details TEXT,
        timestamp TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # 5. Badges table - full schema from Laikaho
    c.execute('''CREATE TABLE IF NOT EXISTS badges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        badge_code TEXT,
        badge_name TEXT,
        badge_description TEXT,
        badge_icon TEXT,
        badge_color TEXT,
        badge_category TEXT,
        awarded_date TEXT,
        progress INTEGER DEFAULT 100,
        progress_target INTEGER DEFAULT 100,
        UNIQUE(user_id, badge_code),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # 6. Achievement progress table - from Laikaho
    c.execute('''CREATE TABLE IF NOT EXISTS achievement_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        achievement_code TEXT,
        current_value INTEGER DEFAULT 0,
        target_value INTEGER,
        updated_date TEXT,
        UNIQUE(user_id, achievement_code),
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # 7. Login streaks table - from Laikaho
    c.execute('''CREATE TABLE IF NOT EXISTS login_streaks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        last_login_date TEXT,
        current_streak INTEGER DEFAULT 0,
        longest_streak INTEGER DEFAULT 0,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # 8. Challenge completions table - with columns from task spec
    c.execute('''CREATE TABLE IF NOT EXISTS challenge_completions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        day_index INTEGER,
        week_number INTEGER,
        year INTEGER,
        avg_score REAL,
        results TEXT,
        completed_at TEXT,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    # Migration: add hide_guide_tip column if it doesn't exist (for existing databases)
    try:
        c.execute('ALTER TABLE users ADD COLUMN hide_guide_tip INTEGER DEFAULT 0')
        conn.commit()
    except Exception:
        pass  # Column already exists

    # Migration: add show_in_ranking column if it doesn't exist
    try:
        c.execute('ALTER TABLE users ADD COLUMN show_in_ranking INTEGER DEFAULT 1')
        conn.commit()
    except Exception:
        pass  # Column already exists

    # Migration: add preferred_language column if it doesn't exist
    try:
        c.execute("ALTER TABLE users ADD COLUMN preferred_language TEXT DEFAULT 'zh'")
        conn.commit()
    except Exception:
        pass  # Column already exists

    # Migration: fix broken default avatar URLs
    c.execute("""UPDATE users SET avatar_url = 'https://img.icons8.com/color/96/user-male-circle--v1.png'
                 WHERE avatar_url IS NULL OR avatar_url = '/static/default_avatar.png'""")

    conn.commit()
    conn.close()
    print("Database initialized with all combined features.")


# ==================== User Functions ====================

def create_user(email, password, name, phone='', role='client', preferred_language='zh'):
    """Create user with phone, role support. Therapists get a therapist_code."""
    conn = get_db()
    c = conn.cursor()
    try:
        pw_hash = generate_password_hash(password)
        therapist_code = None
        if role == 'therapist':
            therapist_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

        joined_date = datetime.now().isoformat()
        c.execute('''INSERT INTO users
                     (email, password_hash, name, phone, role, therapist_code, joined_date, current_streak, preferred_language)
                     VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)''',
                  (email, pw_hash, name, phone, role, therapist_code, joined_date, preferred_language))
        user_id = c.lastrowid

        # Initialize login streak record
        c.execute('INSERT INTO login_streaks (user_id, last_login_date, current_streak, longest_streak) VALUES (?, ?, ?, ?)',
                  (user_id, joined_date, 1, 1))

        # Initialize achievement progress
        init_achievement_progress(c, user_id)

        conn.commit()
        return user_id
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def init_achievement_progress(cursor, user_id):
    """Initialize achievement progress for a new user."""
    achievements = [
        ('practice_count', 1),
        ('perfect_score', 1),
        ('streak_days', 1),
        ('mistake_master', 1),
        ('challenge_complete', 1),
    ]
    for code, target in achievements:
        cursor.execute('''
            INSERT OR IGNORE INTO achievement_progress
            (user_id, achievement_code, current_value, target_value, updated_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, code, 0, target, datetime.now().isoformat()))


def verify_user(email, password):
    """Verify user credentials and update login streak."""
    conn = get_db()
    c = conn.cursor()
    user = c.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

    if user and check_password_hash(user['password_hash'], password):
        user_dict = dict(user)
        update_login_streak(c, user_dict['id'])
        conn.commit()
        conn.close()
        return user_dict

    conn.close()
    return None


def get_user_by_id(user_id):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    conn.close()
    return dict(user) if user else None


def update_user_avatar(user_id, avatar_url):
    conn = get_db()
    conn.execute('UPDATE users SET avatar_url = ? WHERE id = ?', (avatar_url, user_id))
    conn.commit()
    conn.close()


def mark_analysis_completed(user_id):
    conn = get_db()
    conn.execute('UPDATE users SET has_completed_analysis = 1 WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()


def update_user_streak(user_id):
    """Update the current_streak and last_practice_date in the users table."""
    conn = get_db()
    c = conn.cursor()
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)
    user = c.execute('SELECT current_streak, last_practice_date FROM users WHERE id = ?', (user_id,)).fetchone()
    if user:
        last_date_str = user['last_practice_date']
        streak = user['current_streak'] or 0
        if last_date_str == today.isoformat():
            pass  # Already practiced today
        elif last_date_str == yesterday.isoformat():
            streak += 1
        else:
            streak = 1
        c.execute('UPDATE users SET current_streak = ?, last_practice_date = ? WHERE id = ?',
                  (streak, today.isoformat(), user_id))
        conn.commit()
    conn.close()


# ==================== Login Streak Functions (Laikaho) ====================

def update_login_streak(cursor, user_id):
    """Update login streak record in login_streaks table."""
    today = datetime.now().date().isoformat()
    streak = cursor.execute('SELECT * FROM login_streaks WHERE user_id = ?', (user_id,)).fetchone()

    if streak:
        last_login = streak['last_login_date'].split('T')[0] if 'T' in streak['last_login_date'] else streak['last_login_date']

        if last_login == today:
            return  # Already logged in today
        elif (datetime.now().date() - datetime.fromisoformat(last_login).date()).days == 1:
            # Consecutive login
            new_streak = streak['current_streak'] + 1
            longest = max(new_streak, streak['longest_streak'])
            cursor.execute('''
                UPDATE login_streaks
                SET last_login_date = ?, current_streak = ?, longest_streak = ?
                WHERE user_id = ?
            ''', (datetime.now().isoformat(), new_streak, longest, user_id))
            update_achievement_progress(cursor, user_id, 'streak_days', new_streak)
        else:
            # Streak broken
            cursor.execute('''
                UPDATE login_streaks
                SET last_login_date = ?, current_streak = 1
                WHERE user_id = ?
            ''', (datetime.now().isoformat(), user_id))
    else:
        cursor.execute('''
            INSERT INTO login_streaks (user_id, last_login_date, current_streak, longest_streak)
            VALUES (?, ?, 1, 1)
        ''', (user_id, datetime.now().isoformat()))


def get_login_streak(user_id):
    """Get login streak record."""
    conn = get_db()
    streak = conn.execute('SELECT * FROM login_streaks WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    if streak:
        return dict(streak)
    return {'current_streak': 0, 'longest_streak': 0, 'last_login_date': None}


# ==================== Achievement & Badge Functions (Laikaho) ====================

def update_achievement_progress(cursor, user_id, achievement_code, new_value):
    """Update achievement progress and check if badge should be awarded."""
    cursor.execute('''
        UPDATE achievement_progress
        SET current_value = ?, updated_date = ?
        WHERE user_id = ? AND achievement_code = ?
    ''', (new_value, datetime.now().isoformat(), user_id, achievement_code))

    progress = cursor.execute('''
        SELECT * FROM achievement_progress
        WHERE user_id = ? AND achievement_code = ?
    ''', (user_id, achievement_code)).fetchone()

    if progress and progress['current_value'] >= progress['target_value']:
        badge = get_badge_by_achievement(achievement_code, progress['target_value'])
        if badge:
            existing = cursor.execute('''
                SELECT id FROM badges WHERE user_id = ? AND badge_code = ?
            ''', (user_id, badge['code'])).fetchone()
            if not existing:
                cursor.execute('''
                    INSERT INTO badges
                    (user_id, badge_code, badge_name, badge_description, badge_icon, badge_color, badge_category, awarded_date, progress, progress_target)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (user_id, badge['code'], badge['name'], badge['description'],
                      badge['icon'], badge['color'], badge['category'],
                      datetime.now().isoformat(), 100, 100))


# ==================== Badge Translation Map ====================
BADGE_TRANSLATIONS = {
    'practice_beginner': {'name': 'Practice Rookie', 'description': 'Complete your first pronunciation practice'},
    'practice_intermediate': {'name': 'Practice Pro', 'description': 'Complete 10 pronunciation practices'},
    'practice_expert': {'name': 'Practice Master', 'description': 'Complete 50 pronunciation practices'},
    'perfect_beginner': {'name': 'First Perfect', 'description': 'Get a perfect score for the first time'},
    'perfect_intermediate': {'name': 'Perfect Pro', 'description': 'Get 5 perfect scores'},
    'perfect_expert': {'name': 'Perfect Legend', 'description': 'Get 10 perfect scores'},
    'streak_bronze': {'name': 'Streak Starter', 'description': 'Log in 3 days in a row'},
    'streak_silver': {'name': 'Streak Builder', 'description': 'Log in 7 days in a row'},
    'streak_gold': {'name': 'Streak Master', 'description': 'Log in 30 days in a row'},
    'mistake_beginner': {'name': 'Error Fixer', 'description': 'Successfully correct a mistake for the first time'},
    'mistake_intermediate': {'name': 'Error Pro', 'description': 'Successfully correct 5 mistakes'},
    'mistake_expert': {'name': 'Error Master', 'description': 'Successfully correct 20 mistakes'},
    'challenge_beginner': {'name': 'Challenge Rookie', 'description': 'Complete your first daily challenge'},
    'challenge_intermediate': {'name': 'Challenge Pro', 'description': 'Complete 7 daily challenges'},
    'challenge_expert': {'name': 'Challenge Master', 'description': 'Complete 30 daily challenges'},
}


def translate_badge(badge_dict, lang='zh'):
    """Translate badge name and description if lang is 'en'."""
    if lang != 'en':
        return badge_dict
    code = badge_dict.get('badge_code') or badge_dict.get('code', '')
    translation = BADGE_TRANSLATIONS.get(code)
    if translation:
        if 'badge_name' in badge_dict:
            badge_dict['badge_name'] = translation['name']
            badge_dict['badge_description'] = translation['description']
        elif 'name' in badge_dict:
            badge_dict['name'] = translation['name']
            badge_dict['description'] = translation['description']
    return badge_dict


def get_badge_by_achievement(achievement_code, target_value):
    """Get badge info based on achievement code and target value."""
    badges_map = {
        ('practice_count', 1): {'code': 'practice_beginner', 'name': '練習初哥', 'description': '完成第一次發音練習', 'icon': '🎯', 'color': 'bronze', 'category': 'practice'},
        ('practice_count', 10): {'code': 'practice_intermediate', 'name': '練習達人', 'description': '完成10次發音練習', 'icon': '⭐', 'color': 'silver', 'category': 'practice'},
        ('practice_count', 50): {'code': 'practice_expert', 'name': '練習大師', 'description': '完成50次發音練習', 'icon': '🏆', 'color': 'gold', 'category': 'practice'},
        ('perfect_score', 1): {'code': 'perfect_beginner', 'name': '滿分初體驗', 'description': '第一次獲得滿分成績', 'icon': '💯', 'color': 'gold', 'category': 'performance'},
        ('perfect_score', 5): {'code': 'perfect_intermediate', 'name': '滿分達人', 'description': '獲得5次滿分成績', 'icon': '🌟', 'color': 'platinum', 'category': 'performance'},
        ('perfect_score', 10): {'code': 'perfect_expert', 'name': '滿分傳奇', 'description': '獲得10次滿分成績', 'icon': '👑', 'color': 'diamond', 'category': 'performance'},
        ('streak_days', 3): {'code': 'streak_bronze', 'name': '堅持初級', 'description': '連續登錄3天', 'icon': '🔥', 'color': 'bronze', 'category': 'streak'},
        ('streak_days', 7): {'code': 'streak_silver', 'name': '堅持中級', 'description': '連續登錄7天', 'icon': '⚡', 'color': 'silver', 'category': 'streak'},
        ('streak_days', 30): {'code': 'streak_gold', 'name': '堅持大師', 'description': '連續登錄30天', 'icon': '🌋', 'color': 'gold', 'category': 'streak'},
        ('mistake_master', 1): {'code': 'mistake_beginner', 'name': '糾錯新手', 'description': '第一次成功糾正錯字', 'icon': '🎯', 'color': 'bronze', 'category': 'improvement'},
        ('mistake_master', 5): {'code': 'mistake_intermediate', 'name': '糾錯達人', 'description': '成功糾正5個錯字', 'icon': '🎪', 'color': 'silver', 'category': 'improvement'},
        ('mistake_master', 20): {'code': 'mistake_expert', 'name': '糾錯大師', 'description': '成功糾正20個錯字', 'icon': '🎭', 'color': 'gold', 'category': 'improvement'},
        ('challenge_complete', 1): {'code': 'challenge_beginner', 'name': '挑戰新手', 'description': '完成第一個每日挑戰', 'icon': '🏅', 'color': 'bronze', 'category': 'challenge'},
        ('challenge_complete', 7): {'code': 'challenge_intermediate', 'name': '挑戰達人', 'description': '完成7個每日挑戰', 'icon': '🥈', 'color': 'silver', 'category': 'challenge'},
        ('challenge_complete', 30): {'code': 'challenge_expert', 'name': '挑戰大師', 'description': '完成30個每日挑戰', 'icon': '🥇', 'color': 'gold', 'category': 'challenge'},
    }
    return badges_map.get((achievement_code, target_value))


def update_practice_count_achievement(cursor, user_id):
    """Update practice count achievement."""
    count = cursor.execute('SELECT COUNT(*) as count FROM practice_sessions WHERE user_id = ?', (user_id,)).fetchone()
    cursor.execute('''
        UPDATE achievement_progress
        SET current_value = ?, updated_date = ?
        WHERE user_id = ? AND achievement_code = 'practice_count'
    ''', (count['count'], datetime.now().isoformat(), user_id))

    for target in [1, 10, 50]:
        if count['count'] >= target:
            badge = get_badge_by_achievement('practice_count', target)
            if badge:
                existing = cursor.execute('SELECT id FROM badges WHERE user_id = ? AND badge_code = ?', (user_id, badge['code'])).fetchone()
                if not existing:
                    cursor.execute('''
                        INSERT INTO badges
                        (user_id, badge_code, badge_name, badge_description, badge_icon, badge_color, badge_category, awarded_date, progress, progress_target)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (user_id, badge['code'], badge['name'], badge['description'],
                          badge['icon'], badge['color'], badge['category'],
                          datetime.now().isoformat(), 100, 100))


def update_perfect_score_achievement(cursor, user_id):
    """Update perfect score achievement."""
    count = cursor.execute('SELECT COUNT(*) as count FROM practice_sessions WHERE user_id = ? AND score >= 90', (user_id,)).fetchone()
    cursor.execute('''
        UPDATE achievement_progress
        SET current_value = ?, updated_date = ?
        WHERE user_id = ? AND achievement_code = 'perfect_score'
    ''', (count['count'], datetime.now().isoformat(), user_id))

    for target in [1, 5, 10]:
        if count['count'] >= target:
            badge = get_badge_by_achievement('perfect_score', target)
            if badge:
                existing = cursor.execute('SELECT id FROM badges WHERE user_id = ? AND badge_code = ?', (user_id, badge['code'])).fetchone()
                if not existing:
                    cursor.execute('''
                        INSERT INTO badges
                        (user_id, badge_code, badge_name, badge_description, badge_icon, badge_color, badge_category, awarded_date, progress, progress_target)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (user_id, badge['code'], badge['name'], badge['description'],
                          badge['icon'], badge['color'], badge['category'],
                          datetime.now().isoformat(), 100, 100))


def update_mistake_master_achievement(cursor, user_id):
    """Update mistake master achievement."""
    count = cursor.execute('''
        SELECT COUNT(*) as count FROM practice_sessions
        WHERE user_id = ? AND json_extract(details, '$.was_mistake') = 1 AND score >= 80
    ''', (user_id,)).fetchone()

    cursor.execute('''
        UPDATE achievement_progress
        SET current_value = ?, updated_date = ?
        WHERE user_id = ? AND achievement_code = 'mistake_master'
    ''', (count['count'], datetime.now().isoformat(), user_id))

    for target in [1, 5, 20]:
        if count['count'] >= target:
            badge = get_badge_by_achievement('mistake_master', target)
            if badge:
                existing = cursor.execute('SELECT id FROM badges WHERE user_id = ? AND badge_code = ?', (user_id, badge['code'])).fetchone()
                if not existing:
                    cursor.execute('''
                        INSERT INTO badges
                        (user_id, badge_code, badge_name, badge_description, badge_icon, badge_color, badge_category, awarded_date, progress, progress_target)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (user_id, badge['code'], badge['name'], badge['description'],
                          badge['icon'], badge['color'], badge['category'],
                          datetime.now().isoformat(), 100, 100))


def get_user_badges(user_id, lang='zh'):
    """Get all badges for a user."""
    conn = get_db()
    badges = conn.execute('SELECT * FROM badges WHERE user_id = ? ORDER BY awarded_date DESC', (user_id,)).fetchall()
    conn.close()

    result = []
    for badge in badges:
        b = dict(badge)
        if 'awarded_date' in b and b['awarded_date']:
            try:
                awarded = datetime.fromisoformat(b['awarded_date'])
                if lang == 'en':
                    b['awarded_date_formatted'] = awarded.strftime('%b %d, %Y')
                else:
                    b['awarded_date_formatted'] = awarded.strftime('%Y年%m月%d日')
            except:
                b['awarded_date_formatted'] = b['awarded_date']
        translate_badge(b, lang)
        result.append(b)
    return result


def get_badge_statistics(user_id, lang='zh'):
    """Get badge statistics for a user."""
    conn = get_db()
    total = conn.execute('SELECT COUNT(*) as count FROM badges WHERE user_id = ?', (user_id,)).fetchone()
    categories = conn.execute('''
        SELECT badge_category, COUNT(*) as count
        FROM badges WHERE user_id = ?
        GROUP BY badge_category
    ''', (user_id,)).fetchall()
    recent = conn.execute('''
        SELECT * FROM badges WHERE user_id = ?
        ORDER BY awarded_date DESC LIMIT 3
    ''', (user_id,)).fetchall()
    progress = conn.execute('''
        SELECT * FROM achievement_progress WHERE user_id = ?
        ORDER BY updated_date DESC
    ''', (user_id,)).fetchall()
    conn.close()

    next_badges = calculate_next_badges(user_id, progress, lang)

    return {
        'total_badges': total['count'] if total else 0,
        'categories': [dict(c) for c in categories],
        'recent_badges': [dict(r) for r in recent],
        'achievement_progress': [dict(p) for p in progress],
        'next_badges': next_badges
    }


def calculate_next_badges(user_id, progress_records, lang='zh'):
    """Calculate the next achievable badges."""
    next_badges = []
    for prog in progress_records:
        p = dict(prog)
        current = p['current_value']
        targets = []
        if p['achievement_code'] == 'practice_count':
            targets = [1, 10, 50, 100]
        elif p['achievement_code'] == 'perfect_score':
            targets = [1, 5, 10, 20]
        elif p['achievement_code'] == 'streak_days':
            targets = [3, 7, 30, 100]
        elif p['achievement_code'] == 'mistake_master':
            targets = [1, 5, 20, 50]
        elif p['achievement_code'] == 'challenge_complete':
            targets = [1, 7, 30, 100]
        else:
            continue

        for target in targets:
            if current < target:
                badge = get_badge_by_achievement(p['achievement_code'], target)
                if badge:
                    badge['current'] = current
                    badge['target'] = target
                    badge['progress'] = int((current / target) * 100)
                    translate_badge(badge, lang)
                    next_badges.append(badge)
                break

    return next_badges


# ==================== Data Storage Functions ====================

def save_analysis_result(user_id, client_report, professional_report, raw_data_json):
    """Save analysis result with dual reports (client + professional)."""
    conn = get_db()
    c = conn.cursor()
    timestamp = datetime.now().isoformat()

    c.execute('''INSERT INTO analysis_reports
                 (user_id, report_content, professional_report, raw_data, created_at)
                 VALUES (?, ?, ?, ?, ?)''',
              (user_id, client_report, professional_report, json.dumps(raw_data_json), timestamp))

    reading_data = raw_data_json.get('reading_data', [])
    for item in reading_data:
        score = item.get('score', 0)
        accuracy = item.get('accuracy_percent', 100)

        c.execute('''INSERT INTO practice_sessions (user_id, exercise_type, score, details, timestamp)
                     VALUES (?, ?, ?, ?, ?)''',
                  (user_id, 'assessment_reading', score, json.dumps(item), timestamp))

        update_practice_count_achievement(c, user_id)
        if score >= 90:
            update_perfect_score_achievement(c, user_id)

        if score < 70 or accuracy < 80:
            c.execute('''INSERT INTO mistakes (user_id, word, target_jyutping, user_jyutping, score, created_at)
                         VALUES (?, ?, ?, ?, ?, ?)''',
                      (user_id, item.get('target', ''), item.get('target_ipa', ''),
                       item.get('transcript', ''), score, timestamp))

    conn.commit()
    conn.close()

    # Update streak for assessment reading entries
    update_user_streak(user_id)


def save_practice_session(user_id, exercise_type, score, details):
    """Save practice session and update streak + achievements."""
    conn = get_db()
    c = conn.cursor()
    timestamp = datetime.now().isoformat()

    c.execute('INSERT INTO practice_sessions (user_id, exercise_type, score, details, timestamp) VALUES (?, ?, ?, ?, ?)',
              (user_id, exercise_type, score, json.dumps(details), timestamp))

    update_practice_count_achievement(c, user_id)
    if score >= 90:
        update_perfect_score_achievement(c, user_id)

    if details and details.get('was_mistake') and score >= 80:
        update_mistake_master_achievement(c, user_id)

    conn.commit()
    conn.close()

    # Update user streak in users table
    update_user_streak(user_id)


def save_challenge_completion(user_id, day_index, avg_score, results):
    """Save challenge completion - Laikaho's version with challenge achievement update."""
    conn = get_db()
    c = conn.cursor()
    now = datetime.now()
    week_number = now.isocalendar()[1]
    year = now.year

    c.execute('''INSERT INTO challenge_completions
                 (user_id, day_index, week_number, year, avg_score, results, completed_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (user_id, day_index, week_number, year, avg_score, json.dumps(results), now.isoformat()))

    # Update challenge achievement
    count = c.execute('SELECT COUNT(*) as count FROM challenge_completions WHERE user_id = ?', (user_id,)).fetchone()
    c.execute('''
        UPDATE achievement_progress
        SET current_value = ?, updated_date = ?
        WHERE user_id = ? AND achievement_code = 'challenge_complete'
    ''', (count['count'], datetime.now().isoformat(), user_id))

    for target in [1, 7, 30]:
        if count['count'] >= target:
            badge = get_badge_by_achievement('challenge_complete', target)
            if badge:
                existing = c.execute('SELECT id FROM badges WHERE user_id = ? AND badge_code = ?', (user_id, badge['code'])).fetchone()
                if not existing:
                    c.execute('''
                        INSERT INTO badges
                        (user_id, badge_code, badge_name, badge_description, badge_icon, badge_color, badge_category, awarded_date, progress, progress_target)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (user_id, badge['code'], badge['name'], badge['description'],
                          badge['icon'], badge['color'], badge['category'],
                          datetime.now().isoformat(), 100, 100))

    conn.commit()
    conn.close()


def get_user_mistakes(user_id):
    conn = get_db()
    mistakes = conn.execute('SELECT * FROM mistakes WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    conn.close()
    return [dict(m) for m in mistakes]


def get_user_stats_from_db(user_id):
    conn = get_db()
    sessions = conn.execute('SELECT * FROM practice_sessions WHERE user_id = ? ORDER BY timestamp DESC', (user_id,)).fetchall()
    conn.close()
    return [dict(s) for s in sessions]


# ==================== Therapist Functions (Pong) ====================

def get_clients_for_therapist(therapist_id):
    conn = get_db()
    clients = conn.execute('SELECT id, name, email, has_completed_analysis, joined_date FROM users WHERE therapist_id = ?', (therapist_id,)).fetchall()
    conn.close()
    return [dict(c) for c in clients]


def get_user_reports(user_id):
    conn = get_db()
    reports = conn.execute('SELECT * FROM analysis_reports WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in reports]


def link_client_to_therapist(client_id, therapist_code):
    """Link client to therapist using therapist_code lookup (Pong's version)."""
    conn = get_db()
    c = conn.cursor()
    therapist = c.execute("SELECT id FROM users WHERE role='therapist' AND therapist_code=?", (therapist_code,)).fetchone()
    if therapist:
        c.execute("UPDATE users SET therapist_id=? WHERE id=?", (therapist['id'], client_id))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False


def add_therapist_comment(report_id, comment):
    conn = get_db()
    conn.execute("UPDATE analysis_reports SET therapist_comment=? WHERE id=?", (comment, report_id))
    conn.commit()
    conn.close()
