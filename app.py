import os
import json
import random
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

import database
from translations import t, get_t, TRANSLATIONS
load_dotenv()

from services import (
    process_audio,
    analyze_pronunciation_azure,
    process_mission_chat,
    generate_assessment_content,
    generate_analysis_report,
    generate_enhanced_report,
    generate_growth_report,
    get_random_exercise,
    get_personalized_exercises,
    get_daily_challenges,
    lookup_meaning
)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'super_secret_key_for_demo')

# ProxyFix middleware (from Mike)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Avatar upload configuration — use absolute path so it works regardless of working directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

database.init_db()

# ==================== i18n Context Processor ====================

@app.context_processor
def inject_translations():
    """Inject translation function and language into all templates."""
    user = get_current_user() if 'user_id' in session else None
    lang = 'zh'
    if user:
        lang = user.get('preferred_language', 'zh') or 'zh'
    elif 'preferred_language' in session:
        lang = session['preferred_language']
    return {'t': get_t(lang), 'lang': lang}

# ==================== Auth & Flow ====================

def get_current_user():
    if 'user_id' in session:
        return database.get_user_by_id(session['user_id'])
    return None

def get_current_lang():
    """Get the current user's preferred language."""
    user = get_current_user()
    if user:
        return user.get('preferred_language', 'zh') or 'zh'
    return session.get('preferred_language', 'zh')

def flow_redirect(user):
    """Redirect based on user role and analysis completion status."""
    # Therapist role goes to therapist dashboard (Pong)
    if user.get('role') == 'therapist':
        return redirect(url_for('therapist_dashboard'))
    if not user['has_completed_analysis']:
        return redirect(url_for('analysis'))
    return redirect(url_for('dashboard'))

# ==================== Auth Routes ====================

@app.route('/')
def index():
    user = get_current_user()
    if user:
        return flow_redirect(user)
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = database.verify_user(request.form.get('email'), request.form.get('password'))
        if user:
            session['user_id'] = user['id']
            session['user_email'] = user['email']
            session['user_name'] = user['name']
            session['preferred_language'] = user.get('preferred_language', 'zh') or 'zh'

            # Badge check on login (Laikaho)
            conn = database.get_db()
            last_check = session.get('last_badge_check')
            if last_check:
                new_badges = conn.execute('''
                    SELECT COUNT(*) as count FROM badges
                    WHERE user_id = ? AND awarded_date > ?
                ''', (user['id'], last_check)).fetchone()
                session['new_badge_count'] = new_badges['count'] if new_badges else 0
            else:
                session['new_badge_count'] = 0
            session['last_badge_check'] = datetime.now().isoformat()
            conn.close()

            # Reset guide tip for new session (Laikaho)
            session['has_visited_dashboard'] = False
            session['show_guide_tip'] = True

            return flow_redirect(database.get_user_by_id(user['id']))
        return render_template('login.html', error=t('login_error', session.get('preferred_language', 'zh')))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        # myfypversion: separate first/last name fields + phone
        last_name = request.form.get('last_name', '').strip()
        first_name = request.form.get('first_name', '').strip()
        full_name = f"{last_name} {first_name}".strip()

        email = request.form.get('email')
        phone = request.form.get('phone', '')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        role = request.form.get('role', 'client')

        preferred_language_early = request.form.get('preferred_language', 'zh')
        lang_t_early = get_t(preferred_language_early if preferred_language_early in ('zh', 'en') else 'zh')

        if password != confirm_password:
            return render_template('register.html', error=lang_t_early('reg_pw_mismatch'))

        if not password or len(password) < 8:
            return render_template('register.html', error=lang_t_early('reg_pw_weak'))

        preferred_language = request.form.get('preferred_language', 'zh')
        if preferred_language not in ('zh', 'en'):
            preferred_language = 'zh'

        # Store language in session for error rendering
        session['preferred_language'] = preferred_language
        lang_t = get_t(preferred_language)

        # Validate therapist invite code
        if role == 'therapist':
            invite_code = request.form.get('invite_code', '').strip().upper()
            valid_code = os.environ.get('THERAPIST_INVITE_CODE', 'THERA2024').upper()
            if invite_code != valid_code:
                return render_template('register.html', error=lang_t('reg_invite_invalid'))

        user_id = database.create_user(email, password, full_name, phone, role, preferred_language)

        if user_id:
            session['user_id'] = user_id
            session['user_email'] = email
            session['user_name'] = full_name
            session['has_visited_dashboard'] = False
            session['show_guide_tip'] = True
            return flow_redirect(database.get_user_by_id(user_id))

        return render_template('register.html', error=lang_t('reg_fail'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ==================== Page Routes ====================

@app.route('/dashboard')
def dashboard():
    user = get_current_user()
    if not user or not user['has_completed_analysis']:
        return redirect(url_for('analysis'))

    # Guide tip logic (Laikaho)
    if not session.get('has_visited_dashboard'):
        session['has_visited_dashboard'] = True
        session['show_guide_tip'] = True

    # Dashboard stats (Laikaho)
    try:
        sessions = database.get_user_stats_from_db(session['user_id'])
        if sessions:
            total = len(sessions)
            avg_score = sum(s['score'] for s in sessions) / total
            best_score = max(s['score'] for s in sessions)
            trend = 'stable'
            if total >= 2:
                if sessions[0]['score'] > sessions[1]['score']:
                    trend = 'improving'
                elif sessions[0]['score'] < sessions[1]['score']:
                    trend = 'declining'
            stats = {
                'total_sessions': total,
                'avg_score': round(avg_score, 1),
                'best_score': best_score,
                'improvement_trend': trend,
                'recent_sessions': sessions[:5]
            }
        else:
            stats = {'total_sessions': 0, 'avg_score': 0, 'best_score': 0, 'improvement_trend': 'stable', 'recent_sessions': []}
    except Exception as e:
        print(f"Dashboard stats error: {e}")
        stats = {'total_sessions': 0, 'avg_score': 0, 'best_score': 0, 'improvement_trend': 'stable', 'recent_sessions': []}

    return render_template('dashboard.html',
                           user=user,
                           page='dashboard',
                           stats=stats,
                           show_tip=(not user.get('hide_guide_tip', 0) and session.get('show_guide_tip', False)))

@app.route('/analysis')
def analysis():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    return render_template('analysis.html', user=user, page='analysis')

@app.route('/therapy')
def therapy():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if not user['has_completed_analysis']:
        return redirect(url_for('analysis'))
    return render_template('therapy.html', user=user, page='therapy')

@app.route('/therapy/chat')
def therapy_chat():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if not user['has_completed_analysis']:
        return redirect(url_for('analysis'))
    return render_template('therapy_chat.html', user=user, page='therapy')

@app.route('/therapy/pronounce')
def therapy_pronounce():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if not user['has_completed_analysis']:
        return redirect(url_for('analysis'))
    return render_template('therapy_pronounce.html', user=user, page='therapy')

@app.route('/therapy/mouth')
def therapy_mouth():
    """Face detection mouth exercise page (Mike)."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if not user['has_completed_analysis']:
        return redirect(url_for('analysis'))
    return render_template('therapy_mouth.html', user=user, page='therapy')

@app.route('/reports')
def reports():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if not user['has_completed_analysis']:
        return redirect(url_for('analysis'))
    return render_template('reports.html', user=user, page='reports')

@app.route('/beginner-guide')
def beginner_guide():
    """Beginner guide page (Laikaho)."""
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    return render_template('beginner_guide.html', user=user, page='guide')

# ==================== Therapist Routes (Pong) ====================

@app.route('/therapist/dashboard')
def therapist_dashboard():
    user = get_current_user()
    if not user or user.get('role') != 'therapist':
        return redirect(url_for('login'))
    clients = database.get_clients_for_therapist(user['id'])
    return render_template('therapist_dashboard.html', user=user, clients=clients)

@app.route('/therapist/client/<int:client_id>')
def therapist_client_view(client_id):
    user = get_current_user()
    if not user or user.get('role') != 'therapist':
        return redirect(url_for('login'))
    client_user = database.get_user_by_id(client_id)
    if not client_user or client_user.get('therapist_id') != user['id']:
        return "Unauthorized Access", 403
    client_reports = database.get_user_reports(client_id)
    return render_template('therapist_client_view.html', user=user, client=client_user, reports=client_reports)

# ==================== Badge Routes (Laikaho) ====================

@app.route('/badges')
def badges():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if not user['has_completed_analysis']:
        return redirect(url_for('analysis'))

    session['new_badge_count'] = 0  # Clear notification when user visits badges page

    lang = get_current_lang()
    badges_list = database.get_user_badges(session['user_id'], lang)
    stats = database.get_badge_statistics(session['user_id'], lang)
    streak = database.get_login_streak(session['user_id'])

    achievement_progress = {}
    achievement_progress_details = []
    for prog in stats.get('achievement_progress', []):
        achievement_progress[prog['achievement_code']] = prog['current_value']
        achievement_progress_details.append(prog)

    next_badges = stats.get('next_badges', [])

    return render_template('badges.html',
                           user=user,
                           page='badges',
                           badges=badges_list,
                           stats=stats,
                           streak=streak,
                           achievement_progress=achievement_progress,
                           achievement_progress_details=achievement_progress_details,
                           next_badges=next_badges)

@app.route('/api/badges/check', methods=['POST'])
def api_check_badges():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    conn = database.get_db()
    c = conn.cursor()
    database.update_practice_count_achievement(c, session['user_id'])
    database.update_perfect_score_achievement(c, session['user_id'])
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/badges/recent', methods=['GET'])
def api_recent_badges():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    conn = database.get_db()
    recent = conn.execute('''
        SELECT * FROM badges
        WHERE user_id = ?
        ORDER BY awarded_date DESC
        LIMIT 1
    ''', (session['user_id'],)).fetchone()
    conn.close()
    if recent:
        return jsonify(dict(recent))
    return jsonify({})

# ==================== Challenge Routes (Laikaho) ====================

@app.route('/challenges')
def challenges():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    if not user['has_completed_analysis']:
        return redirect(url_for('analysis'))

    weekly_data = get_weekly_challenges(session['user_id'])

    return render_template('challenges.html',
                           user=user,
                           page='challenges',
                           weekly_theme=weekly_data['theme'],
                           daily_challenges=weekly_data['challenges'],
                           total_challenges=weekly_data['total'],
                           completed_count=weekly_data['completed'],
                           weekly_progress=weekly_data['progress'])

@app.route('/api/challenge/pronounce', methods=['POST'])
def api_challenge_pronounce():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file'}), 400

    audio_file = request.files['audio']
    target_word = request.form.get('target_word')
    target_ipa = request.form.get('target_ipa')

    result = analyze_pronunciation_azure(audio_file, target_word)
    result['jyutping'] = target_ipa

    return jsonify(result)

@app.route('/api/challenge/complete', methods=['POST'])
def api_challenge_complete():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401

    data = request.json
    day_id = data.get('day_id')
    results = data.get('results', [])
    avg_score = data.get('avg_score', 0)

    database.save_challenge_completion(session['user_id'], day_id, avg_score, results)

    return jsonify({'success': True})

# ==================== API Routes ====================

@app.route('/api/hide-tip', methods=['POST'])
def hide_tip():
    """Hide the beginner guide tip permanently in the database."""
    if 'user_id' in session:
        session['show_guide_tip'] = False
        conn = database.get_db()
        conn.execute('UPDATE users SET hide_guide_tip = 1 WHERE id = ?', (session['user_id'],))
        conn.commit()
        conn.close()
    return jsonify({'success': True})

@app.route('/api/chat_process', methods=['POST'])
def api_chat_process():
    """Smart routing with face detection score adjustment (Mike)."""
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio file'}), 400

    audio_file = request.files['audio']
    target_word = request.form.get('target_word')
    target_ipa = request.form.get('target_ipa')

    # Face detection params (Mike)
    face_detected_str = request.form.get('face_detected')
    lip_movement_str = request.form.get('lip_movement_detected')

    if target_word:
        result = analyze_pronunciation_azure(audio_file, target_word)
        result['jyutping'] = target_ipa
        result['target'] = target_word
    else:
        result = process_audio(audio_file)

    # Face detection score adjustment (Mike)
    if 'error' not in result:
        if face_detected_str is not None and lip_movement_str is not None:
            face_ok = face_detected_str.lower() == 'true'
            lip_ok = lip_movement_str.lower() == 'true'
            penalty = 0
            feedback_note = ""
            if not face_ok:
                penalty += 10
                feedback_note += "我哋檢測唔到你嘅面部，"
            if not lip_ok:
                penalty += 10
                feedback_note += "我哋檢測唔到你嘅嘴唇郁動，"
            if penalty > 0:
                original_score = result.get('score') or result.get('accuracy_percent', 0)
                if original_score is None:
                    original_score = 0
                new_score = max(0, original_score - penalty)
                result['score'] = new_score
                result['accuracy_percent'] = new_score
                feedback_note += "下次記得對住鏡頭講嘢啊！"
                if 'humanPerception' in result and isinstance(result['humanPerception'], str):
                    result['humanPerception'] = result['humanPerception'] + " " + feedback_note
                else:
                    result['humanPerception'] = feedback_note

    return jsonify(result)

@app.route('/api/mission_chat_process', methods=['POST'])
def api_mission_chat_process():
    if 'audio' not in request.files:
        return jsonify({'error': 'No audio'}), 400
    try:
        current_mission_words = json.loads(request.form.get('current_mission_words', '[]'))
    except:
        current_mission_words = []

    result = process_mission_chat(
        request.files['audio'],
        request.form.get('topic'),
        current_mission_words,
        request.form.get('history')
    )
    return jsonify(result)

@app.route('/api/analysis/init_data', methods=['GET'])
def api_analysis_init_data():
    return jsonify(generate_assessment_content())

@app.route('/api/submit_analysis_results', methods=['POST'])
def api_submit_analysis_results():
    """Submit analysis results - services returns dict with client_report and professional_report."""
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    data = request.json

    # generate_analysis_report returns a dict with 'client_report' and 'professional_report' (Pong's version)
    reports = generate_analysis_report(data.get('reading_data'), data.get('conversation_data'))

    # save_analysis_result takes (user_id, client_report, professional_report, raw_data)
    database.save_analysis_result(
        session['user_id'],
        reports.get('client_report', '簡易報告生成失敗'),
        reports.get('professional_report', '專業報告生成失敗'),
        data
    )
    database.mark_analysis_completed(session['user_id'])

    # Reset guide tip (Laikaho)
    session['has_visited_dashboard'] = False
    session['show_guide_tip'] = True

    return jsonify({'success': True, 'report': reports.get('client_report')})

@app.route('/api/reports/generate_growth', methods=['POST'])
def api_reports_generate_growth():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401

    conn = database.get_db()
    cursor = conn.execute('SELECT * FROM practice_sessions WHERE user_id = ? ORDER BY timestamp ASC', (session['user_id'],))
    rows = cursor.fetchall()
    conn.close()

    if len(rows) < 2:
        return jsonify({'success': False, 'message': '練習記錄不足，請至少進行 2 次練習以便分析進步趨勢。'})

    history = []
    for r in rows:
        try:
            details = json.loads(r['details'])
            word = details.get('target', details.get('word', '練習'))
        except:
            word = "練習"
        history.append({
            'date': r['timestamp'],
            'score': r['score'],
            'type': r['exercise_type'],
            'word': word
        })

    report_content = generate_growth_report(history)
    return jsonify({'success': True, 'report': report_content})

@app.route('/api/reports/history', methods=['GET'])
def api_reports_history():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    conn = database.get_db()
    # Include professional_report and therapist_comment (Pong)
    reports = conn.execute(
        'SELECT id, report_content, professional_report, therapist_comment, created_at FROM analysis_reports WHERE user_id = ? ORDER BY created_at DESC',
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return jsonify({'reports': [dict(r) for r in reports]})

@app.route('/api/reports/enhanced', methods=['POST'])
def api_reports_enhanced():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    sessions = database.get_user_stats_from_db(session['user_id'])
    if not sessions:
        return jsonify({'report': '暫無練習數據。'})
    data = []
    for s in sessions[:15]:
        try:
            det = json.loads(s['details'])
            data.append({'transcript': det.get('transcript'), 'score': s['score']})
        except:
            pass
    report = generate_enhanced_report(data, session.get('user_email', ''))
    return jsonify({'report': report})

@app.route('/api/practice/submit', methods=['POST'])
def api_practice_submit():
    """Submit practice result and check for new badges (Laikaho)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    data = request.json
    database.save_practice_session(
        session['user_id'],
        data.get('exercise_type'),
        data.get('score'),
        data.get('details')
    )

    # Check for new badges (Laikaho)
    conn = database.get_db()
    new_badge = conn.execute('''
        SELECT * FROM badges
        WHERE user_id = ?
        ORDER BY awarded_date DESC
        LIMIT 1
    ''', (session['user_id'],)).fetchone()

    if new_badge:
        try:
            awarded = datetime.fromisoformat(new_badge['awarded_date'])
            now = datetime.now()
            if (now - awarded).total_seconds() < 5:
                session['new_badge_count'] = session.get('new_badge_count', 0) + 1
                conn.close()
                return jsonify({
                    'success': True,
                    'new_badge': {
                        'name': new_badge['badge_name'],
                        'icon': new_badge['badge_icon'],
                        'description': new_badge['badge_description']
                    }
                })
        except:
            pass
    conn.close()

    return jsonify({'success': True})

@app.route('/api/user_stats', methods=['GET'])
def api_user_stats():
    """User stats including streak (myfypversion)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401

    user_info = database.get_user_by_id(session['user_id'])
    streak = user_info.get('current_streak', 0) if user_info else 0
    sessions = database.get_user_stats_from_db(session['user_id'])

    total = len(sessions)
    if total == 0:
        return jsonify({
            'total_sessions': 0,
            'avg_score': 0,
            'improvement_trend': 'stable',
            'best_score': 0,
            'current_streak': streak
        })

    avg = sum(s['score'] for s in sessions) / total
    best = max(s['score'] for s in sessions)
    trend = 'stable'
    if total >= 2:
        if sessions[0]['score'] > sessions[1]['score']:
            trend = 'improving'
        elif sessions[0]['score'] < sessions[1]['score']:
            trend = 'declining'

    return jsonify({
        'total_sessions': total,
        'avg_score': int(avg),
        'improvement_trend': trend,
        'best_score': best,
        'last_session': sessions[0]['timestamp'] if sessions else None,
        'current_streak': streak
    })

@app.route('/api/profile/update_name', methods=['POST'])
def api_update_name():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    name = (request.json or {}).get('name', '').strip()
    if not name:
        user = database.get_user_by_id(session['user_id'])
        ul = (user or {}).get('preferred_language', 'zh')
        return jsonify({'error': t('api_name_empty', ul)}), 400
    conn = database.get_db()
    conn.execute('UPDATE users SET name = ? WHERE id = ?', (name, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'name': name})

@app.route('/api/profile/change_password', methods=['POST'])
def api_change_password():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    data = request.json or {}
    current_pw = data.get('current_password', '')
    new_pw = data.get('new_password', '')
    user = database.get_user_by_id(session['user_id'])
    ul = (user or {}).get('preferred_language', 'zh')
    if not current_pw or not new_pw:
        return jsonify({'error': t('api_fill_all', ul)}), 400
    if len(new_pw) < 8:
        return jsonify({'error': t('api_pw_min', ul)}), 400
    from werkzeug.security import check_password_hash, generate_password_hash
    if not check_password_hash(user['password_hash'], current_pw):
        return jsonify({'error': t('api_pw_wrong', ul)}), 400
    conn = database.get_db()
    conn.execute('UPDATE users SET password_hash = ? WHERE id = ?',
                 (generate_password_hash(new_pw), session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/profile/toggle_ranking', methods=['POST'])
def api_toggle_ranking():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    conn = database.get_db()
    user = conn.execute('SELECT show_in_ranking FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    new_val = 0 if user['show_in_ranking'] else 1
    conn.execute('UPDATE users SET show_in_ranking = ? WHERE id = ?', (new_val, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'show_in_ranking': bool(new_val)})

@app.route('/api/profile/set_language', methods=['POST'])
def api_set_language():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    lang = (request.json or {}).get('language', 'zh')
    if lang not in ('zh', 'en'):
        lang = 'zh'
    conn = database.get_db()
    conn.execute('UPDATE users SET preferred_language = ? WHERE id = ?', (lang, session['user_id']))
    conn.commit()
    conn.close()
    session['preferred_language'] = lang
    return jsonify({'success': True, 'language': lang})

def _get_therapist_name(therapist_id):
    if not therapist_id:
        return None
    conn = database.get_db()
    t = conn.execute('SELECT name FROM users WHERE id = ?', (therapist_id,)).fetchone()
    conn.close()
    return t['name'] if t else None

@app.route('/api/profile/stats', methods=['GET'])
def api_profile_stats():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    user = database.get_user_by_id(session['user_id'])
    sessions = database.get_user_stats_from_db(session['user_id'])
    badges = database.get_user_badges(session['user_id'], get_current_lang())
    streak = database.get_login_streak(session['user_id'])
    total = len(sessions)
    avg = round(sum(s['score'] for s in sessions) / total, 1) if total else 0
    best = max((s['score'] for s in sessions), default=0)
    return jsonify({
        'name': user['name'],
        'email': user['email'],
        'avatar_url': user.get('avatar_url') or '',
        'joined_date': user.get('joined_date', '')[:10] if user.get('joined_date') else '',
        'role': user.get('role', 'client'),
        'total_sessions': total,
        'avg_score': avg,
        'best_score': best,
        'badge_count': len(badges),
        'current_streak': streak.get('current_streak', 0),
        'longest_streak': streak.get('longest_streak', 0),
        'show_in_ranking': bool(user.get('show_in_ranking', 1)),
        'role': user.get('role', 'client'),
        'therapist_id': user.get('therapist_id'),
        'therapist_name': _get_therapist_name(user.get('therapist_id')),
    })

@app.route('/api/leaderboard', methods=['GET'])
def api_leaderboard():
    """Daily leaderboard ranked by total practice sessions."""
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    conn = database.get_db()
    rows = conn.execute('''
        SELECT u.id, u.name, u.avatar_url, COUNT(ps.id) as total_sessions
        FROM users u
        LEFT JOIN practice_sessions ps ON u.id = ps.user_id
        WHERE u.role = 'client' AND u.show_in_ranking = 1
        GROUP BY u.id
        ORDER BY total_sessions DESC
        LIMIT 10
    ''').fetchall()
    conn.close()
    current_user_id = session['user_id']
    result = []
    for i, row in enumerate(rows):
        result.append({
            'rank': i + 1,
            'name': row['name'],
            'avatar_url': row['avatar_url'] or 'https://img.icons8.com/color/96/user-male-circle--v1.png',
            'total_sessions': row['total_sessions'],
            'is_me': row['id'] == current_user_id
        })
    return jsonify({'leaderboard': result})

@app.route('/api/user/history', methods=['GET'])
def api_user_history():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    sessions = database.get_user_stats_from_db(session['user_id'])
    clean = []
    for s in sessions:
        d = dict(s)
        try:
            d['details'] = json.loads(s['details'])
        except:
            d['details'] = {}
        clean.append(d)
    return jsonify({'recent_records': clean})

@app.route('/api/user/history_full', methods=['GET'])
def api_user_history_full():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401

    sessions = database.get_user_stats_from_db(session['user_id'])
    clean_data = []
    for s in sessions[:50]:
        try:
            det = json.loads(s['details'])
            title = (det.get('target') or det.get('word') or det.get('text')
                     or det.get('exercise', {}).get('word')
                     or det.get('exercise', {}).get('text')
                     or det.get('exercise', {}).get('title')
                     or '練習')
            if isinstance(title, list):
                title = ' '.join(title)
        except:
            title = '練習'
            det = {}

        clean_data.append({
            'id': s['id'],
            'timestamp': s['timestamp'],
            'type': s['exercise_type'],
            'score': s['score'],
            'title': title
        })

    return jsonify({'history': clean_data})

@app.route('/api/user/chart_data', methods=['GET'])
def api_user_chart_data():
    """Chart data API (myfypversion)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    conn = database.get_db()
    cursor = conn.execute('''
        SELECT substr(timestamp, 1, 10) as date, AVG(score) as avg_score
        FROM practice_sessions WHERE user_id = ?
        GROUP BY substr(timestamp, 1, 10)
        ORDER BY date DESC LIMIT 7
    ''', (session['user_id'],))
    rows = cursor.fetchall()
    conn.close()
    chart_data = [{'date': r['date'], 'score': round(r['avg_score'], 1)} for r in rows][::-1]
    return jsonify({'success': True, 'chart_data': chart_data})

@app.route('/api/export/data', methods=['GET'])
def api_export_data():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    return api_user_history()

@app.route('/api/exercises/random', methods=['GET'])
def api_exercises_random():
    if 'user_id' in session:
        mistakes = database.get_user_mistakes(session['user_id'])
        if mistakes and len(mistakes) > 0 and random.random() < 0.4:
            m = random.choice(mistakes)
            return jsonify({'word': m['word'], 'ipa': m['target_jyutping'], 'category': 'mistakes', 'meaning': lookup_meaning(m['word'])})

    level = request.args.get('level', 'beginner')
    category = request.args.get('category')
    exercise = get_random_exercise(level, category)
    if not exercise:
        exercise = {'word': '你好', 'ipa': 'nei5 hou2', 'meaning': 'Hello'}
    if not exercise.get('meaning'):
        exercise['meaning'] = lookup_meaning(exercise.get('word', ''))
    return jsonify(exercise)

@app.route('/api/challenges/daily', methods=['GET'])
def api_challenges_daily():
    lang = get_current_lang()
    return jsonify({'challenges': get_daily_challenges(lang)})

@app.route('/api/exercises/personalized', methods=['GET'])
def api_exercises_personalized():
    if 'user_id' not in session:
        return jsonify({'recommendations': []})
    mistakes = database.get_user_mistakes(session['user_id'])
    if not mistakes:
        return jsonify({'recommendations': []})
    recs = []
    for m in mistakes[:3]:
        recs.append({'word': m['word'], 'ipa': m['target_jyutping'], 'description': '針對錯字訓練'})
    return jsonify({'recommendations': recs})

@app.route('/api/upload_avatar', methods=['POST'])
def api_upload_avatar():
    """Avatar upload (myfypversion)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    if 'avatar' not in request.files:
        return jsonify({'error': '無搵到檔案'}), 400

    file = request.files['avatar']
    if file.filename == '':
        return jsonify({'error': '未選擇檔案'}), 400

    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = secure_filename(f"user_{session['user_id']}_avatar.{ext}")
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        avatar_url = '/static/uploads/' + filename
        database.update_user_avatar(session['user_id'], avatar_url)
        return jsonify({'success': True, 'avatar_url': avatar_url})

    return jsonify({'error': '只支援 PNG, JPG, JPEG, GIF 格式'}), 400

@app.route('/api/client/unbind', methods=['POST'])
def api_client_unbind():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    conn = database.get_db()
    conn.execute('UPDATE users SET therapist_id = NULL WHERE id = ?', (session['user_id'],))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/therapist/unbind_client', methods=['POST'])
def api_therapist_unbind_client():
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    user = get_current_user()
    if not user or user.get('role') != 'therapist':
        return jsonify({'error': 'Forbidden'}), 403
    client_id = (request.json or {}).get('client_id')
    if not client_id:
        return jsonify({'error': 'Missing client_id'}), 400
    conn = database.get_db()
    conn.execute('UPDATE users SET therapist_id = NULL WHERE id = ? AND therapist_id = ?',
                 (client_id, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/client/bind', methods=['POST'])
def api_client_bind():
    """Bind client to therapist (Pong)."""
    user = get_current_user()
    if not user:
        return jsonify({'error': 'Auth'}), 401
    success = database.link_client_to_therapist(user['id'], (request.json or {}).get('code'))
    return jsonify({"success": success})

@app.route('/api/therapist/comment', methods=['POST'])
def api_therapist_comment():
    """Save therapist comment on report (Pong)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Auth'}), 401
    user = get_current_user()
    if not user or user.get('role') != 'therapist':
        return jsonify({'error': 'Forbidden'}), 403
    data = request.json or {}
    report_id = data.get('report_id')
    comment = data.get('comment')
    if not report_id or comment is None:
        return jsonify({'error': 'Missing report_id or comment'}), 400
    database.add_therapist_comment(report_id, comment)
    return jsonify({"success": True})

# ==================== Helper Functions (Laikaho) ====================

def get_weekly_challenges(user_id):
    """Generate weekly challenge content."""
    themes = [
        {"name": "水果大冒險", "words": ["蘋果", "香蕉", "橙", "西瓜", "提子"]},
        {"name": "動物園之旅", "words": ["獅子", "老虎", "大象", "熊貓", "長頸鹿"]},
        {"name": "超級市場", "words": ["麵包", "牛奶", "餅乾", "糖果", "雪糕"]},
        {"name": "遊樂場", "words": ["滑梯", "鞦韆", "蹺蹺板", "氹氹轉", "沙池"]},
        {"name": "交通工具", "words": ["巴士", "的士", "地鐵", "火車", "飛機"]}
    ]

    week_number = datetime.now().isocalendar()[1]
    theme = themes[week_number % len(themes)]

    challenges = []
    days = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

    for i, day in enumerate(days):
        day_words = []
        for j in range(3):
            word_idx = (i * 3 + j) % len(theme["words"])
            word = theme["words"][word_idx]
            jyutping = get_jyutping_for_word(word)
            day_words.append({"word": word, "ipa": jyutping})

        completed = check_day_completed(user_id, i) if user_id else False

        challenges.append({
            "id": i,
            "day_name": day,
            "title": f"第{i+1}日：{theme['name']} - 第{i+1}關",
            "description": f"今日要練習 {', '.join([w['word'] for w in day_words])} 呢幾個詞語。",
            "target_words": day_words,
            "completed": completed
        })

    completed_count = sum(1 for c in challenges if c["completed"])
    total = len(challenges)
    progress = (completed_count / total * 100) if total > 0 else 0

    return {
        "theme": theme["name"],
        "challenges": challenges,
        "total": total,
        "completed": completed_count,
        "progress": progress
    }


def get_jyutping_for_word(word):
    """Simple dictionary lookup for Jyutping."""
    jyutping_dict = {
        "蘋果": "ping4 gwo2", "香蕉": "hoeng1 ziu1", "橙": "caang2", "西瓜": "sai1 gwaa1", "提子": "tai4 zi2",
        "獅子": "si1 zi2", "老虎": "lou5 fu2", "大象": "daai6 zoeng6", "熊貓": "hung4 maau1", "長頸鹿": "coeng4 geng2 luk6",
        "麵包": "min6 baau1", "牛奶": "ngau4 naai5", "餅乾": "beng2 gon1", "糖果": "tong4 gwo2", "雪糕": "syut3 gou1",
        "滑梯": "waat6 tai1", "鞦韆": "cau1 cin1", "蹺蹺板": "hiu1 hiu1 baan2", "氹氹轉": "tam5 tam5 zyun3", "沙池": "saa1 ci4",
        "巴士": "baa1 si2", "的士": "dik1 si2", "地鐵": "dei6 tit3", "火車": "fo2 ce1", "飛機": "fei1 gei1"
    }
    return jyutping_dict.get(word, "")


def check_day_completed(user_id, day_index):
    """Check if a challenge day has been completed this week."""
    conn = database.get_db()
    week_number = datetime.now().isocalendar()[1]
    year = datetime.now().year
    completed = conn.execute('''
        SELECT COUNT(*) as count
        FROM challenge_completions
        WHERE user_id = ? AND day_index = ? AND week_number = ? AND year = ?
    ''', (user_id, day_index, week_number, year)).fetchone()
    conn.close()
    return completed and completed['count'] > 0
@app.route('/api/synthesize', methods=['POST'])
def api_synthesize():
    """生成廣東話示範發音 API"""
    if 'user_id' not in session: 
        return jsonify({'error': 'Auth'}), 401
        
    text = request.json.get('text')
    if not text:
        return jsonify({'error': 'No text provided'}), 400
        
    # Call services.py 裡面的 Azure TTS
    import base64
    from services import synthesize_cantonese_speech
    
    audio_data = synthesize_cantonese_speech(text)
    if audio_data:
        # 將二進制音頻轉成 Base64 傳畀前端
        b64_audio = base64.b64encode(audio_data).decode('utf-8')
        return jsonify({'audio_base64': b64_audio})
        
    return jsonify({'error': 'Synthesis failed'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000, debug=True)
