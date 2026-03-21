from flask import Flask, render_template, request
import pymysql
import datetime

app = Flask(__name__)

# 数据库配置（根据实际情况修改）
DB_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'user': 'root',
    'password': 'root',
    'database': 'adata',
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

def get_db_connection():
    return pymysql.connect(**DB_CONFIG)

def get_calendar_data(target_date=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if target_date:
            year_month = target_date[:7]
            sql = "SELECT event_date, title, content FROM finance_calendar WHERE event_date LIKE %s ORDER BY event_date"
            cursor.execute(sql, (f"{year_month}%",))
        else:
            year_month = datetime.datetime.now().strftime("%Y-%m")
            sql = "SELECT event_date, title, content FROM finance_calendar WHERE event_date LIKE %s ORDER BY event_date"
            cursor.execute(sql, (f"{year_month}%",))
        
        results = cursor.fetchall()
        date_groups = {}
        for row in results:
            date_str = row['event_date']
            if date_str not in date_groups:
                date_groups[date_str] = []
            date_groups[date_str].append({
                'title': row['title'],
                'content': row['content']
            })
        return date_groups
    finally:
        cursor.close()
        conn.close()

def get_prev_next_month(current_date):
    year, month = map(int, current_date.split('-'))
    prev_month = month - 1 if month > 1 else 12
    prev_year = year - 1 if month == 1 else year
    next_month = month + 1 if month < 12 else 1
    next_year = year + 1 if month == 12 else year
    return (
        f"{prev_year}-{prev_month:02d}",
        f"{next_year}-{next_month:02d}"
    )

# 注册模板全局函数：计算日期对应的星期
@app.template_global()
def week_day(date_str):
    # 解析日期字符串为 datetime 对象
    date = datetime.datetime.strptime(str(date_str), "%Y-%m-%d")
    # weeks 索引：0=周一, 1=周二, ..., 6=周日
    weeks = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return weeks[date.weekday()]  # weekday() 返回 0-6，对应周一到周日

@app.route('/')
def calendar():
    target_date = request.args.get('date')
    date_groups = get_calendar_data(target_date)
    current_month = target_date[:7] if target_date else datetime.datetime.now().strftime("%Y-%m")
    prev_month, next_month = get_prev_next_month(current_month)
    return render_template(
        'calendar.html',
        date_groups=date_groups,
        current_month=current_month,
        prev_month=prev_month,
        next_month=next_month
    )

if __name__ == '__main__':
    app.run(debug=True)