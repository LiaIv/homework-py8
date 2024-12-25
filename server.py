from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.error import HTTPError
import os
import json
import urllib.request
import urllib.parse
import html
import logging
import re

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("server.log"),
        logging.StreamHandler()
    ]
)

# Получение OAuth-токена из переменной окружения и его очистка
YANDEX_DISK_TOKEN = os.getenv('YANDEX_DISK_TOKEN')

if not YANDEX_DISK_TOKEN:
    logging.critical("Не найдена переменная окружения 'YANDEX_DISK_TOKEN'")
    raise EnvironmentError("Не найдена переменная окружения 'YANDEX_DISK_TOKEN'")

YANDEX_DISK_TOKEN = YANDEX_DISK_TOKEN.strip()

# Проверка, что токен содержит только допустимые символы
if not all(ord(c) < 128 for c in YANDEX_DISK_TOKEN):
    logging.critical("OAuth-токен содержит недопустимые символы. Убедитесь, что он состоит только из ASCII-символов.")
    raise ValueError("OAuth-токен содержит недопустимые символы. Убедитесь, что он состоит только из ASCII-символов.")

# Папка для временного хранения загружаемых файлов
UPLOAD_DIR = 'uploads'

# Путь к папке с шаблонами
TEMPLATE_DIR = 'templates'

# Убедитесь, что папка для загрузок существует
os.makedirs(UPLOAD_DIR, exist_ok=True)

def get_uploaded_files(token):
    url = "https://cloud-api.yandex.net/v1/disk/resources/files"
    headers = {
        "Authorization": f"OAuth {token}"
    }
    params = {
        "limit": 100
    }
    files = []
    while True:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(url + "?" + query, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode())
                items = data.get('items', [])
                files.extend([item['name'] for item in items])
                if '_links' in data and 'next' in data['_links']:
                    url = data['_links']['next']['href']
                    params = {}
                else:
                    break
        except HTTPError as e:
            logging.error(f"HTTP ошибка при получении списка файлов: {e.code} {e.reason}")
            break
        except Exception as e:
            logging.error(f"Ошибка при получении списка файлов: {e}")
            break
    return files

def upload_file_to_yandex_disk(token, file_path, yandex_disk_path):
    upload_url_request = f"https://cloud-api.yandex.net/v1/disk/resources/upload?path={urllib.parse.quote(yandex_disk_path)}&overwrite=true"
    headers = {
        "Authorization": f"OAuth {token}"
    }
    request = urllib.request.Request(upload_url_request, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode())
            upload_url = data['href']
            logging.info(f"Получена ссылка для загрузки: {upload_url}")
    except HTTPError as e:
        logging.error(f"HTTP ошибка при получении ссылки для загрузки: {e.code} {e.reason}")
        return False
    except Exception as e:
        logging.error(f"Ошибка при получении ссылки для загрузки: {e}")
        return False

    # Загрузка файла
    try:
        with open(file_path, 'rb') as f:
            upload_request = urllib.request.Request(upload_url, data=f.read(), method='PUT')
            with urllib.request.urlopen(upload_request, timeout=30) as upload_response:
                if upload_response.status in (201, 202):
                    logging.info(f"Файл {yandex_disk_path} успешно загружен.")
                else:
                    logging.error(f"Неожиданный статус ответа при загрузке: {upload_response.status}")
                    return False
    except HTTPError as e:
        logging.error(f"HTTP ошибка при загрузке файла: {e.code} {e.reason}")
        return False
    except Exception as e:
        logging.error(f"Ошибка при загрузке файла: {e}")
        return False

    # Дополнительная проверка наличия файла на Яндекс.Диске
    if not check_file_exists_on_yandex_disk(token, yandex_disk_path):
        logging.error(f"Файл {yandex_disk_path} не найден на Яндекс.Диске после загрузки.")
        return False

    return True

def check_file_exists_on_yandex_disk(token, yandex_disk_path):
    url = f"https://cloud-api.yandex.net/v1/disk/resources?path={urllib.parse.quote(yandex_disk_path)}"
    headers = {
        "Authorization": f"OAuth {token}"
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status == 200:
                logging.info(f"Файл {yandex_disk_path} подтвержден на Яндекс.Диске.")
                return True
    except HTTPError as e:
        if e.code == 404:
            logging.error(f"Файл {yandex_disk_path} не существует на Яндекс.Диске.")
        else:
            logging.error(f"HTTP ошибка при проверке файла: {e.code} {e.reason}")
    except Exception as e:
        logging.error(f"Ошибка при проверке файла на Яндекс.Диске: {e}")
    return False

def sanitize_filename(filename):
    return os.path.basename(filename)

def parse_multipart(content_type, rfile, content_length):
    """
    Простая функция для парсинга multipart/form-data.
    Возвращает словарь с полями формы.
    """
    boundary = re.findall('boundary=(.*)', content_type)
    if not boundary:
        return {}
    boundary = boundary[0]
    boundary_bytes = boundary.encode('utf-8')
    delimiter = b'--' + boundary_bytes
    terminator = b'--' + boundary_bytes + b'--'

    remaining_bytes = content_length
    data = rfile.read(content_length)
    remaining_bytes -= len(data)

    parts = data.split(delimiter)
    form_data = {}

    for part in parts:
        if not part or part == b'--' or part == b'--\r\n':
            continue
        if part.startswith(b'\r\n'):
            part = part[2:]
        if part.endswith(b'\r\n'):
            part = part[:-2]
        if part == b'--':
            break

        # Разделение заголовков и тела
        try:
            headers, body = part.split(b'\r\n\r\n', 1)
        except ValueError:
            continue

        # Парсинг заголовков
        header_lines = headers.decode('utf-8').split('\r\n')
        header_dict = {}
        for line in header_lines:
            key, value = line.split(':', 1)
            header_dict[key.strip().lower()] = value.strip()

        # Обработка Content-Disposition
        if 'content-disposition' in header_dict:
            disposition, *params = header_dict['content-disposition'].split(';')
            disposition = disposition.strip().lower()
            param_dict = {}
            for param in params:
                if '=' in param:
                    key, val = param.strip().split('=', 1)
                    param_dict[key.strip()] = val.strip().strip('"')
            name = param_dict.get('name')
            filename = param_dict.get('filename')
            if filename:
                form_data[name] = {
                    'filename': filename,
                    'content': body
                }
            else:
                form_data[name] = body.decode('utf-8')

    return form_data

class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path.startswith('/'):
            self.handle_index()
        else:
            self.send_error(404, "Страница не найдена")

    def do_POST(self):
        if self.path == '/upload':
            self.handle_upload()
        else:
            self.send_error(404, "Страница не найдена")

    def handle_index(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        message = params.get('message', [''])[0]
        message_type = params.get('type', ['success'])[0]

        try:
            all_files = os.listdir(UPLOAD_DIR)
            logging.info(f"Найдено {len(all_files)} файлов в директории загрузок.")
        except Exception as e:
            logging.error(f"Ошибка при чтении директории загрузок: {e}")
            all_files = []

        uploaded_files = get_uploaded_files(YANDEX_DISK_TOKEN)
        file_list_html = ""
        for file in all_files:
            if file in uploaded_files:
                file_list_html += f'<li class="uploaded">{self.escape_html(file)}</li>'
            else:
                file_list_html += f'<li>{self.escape_html(file)}</li>'

        template_path = os.path.join(TEMPLATE_DIR, 'index.html')
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                html_template = f.read()
            logging.info(f"Шаблон {template_path} успешно загружен.")
        except FileNotFoundError:
            logging.error("Шаблон не найден.")
            self.send_error(500, "Шаблон не найден")
            return
        except Exception as e:
            logging.error(f"Ошибка при загрузке шаблона: {e}")
            self.send_error(500, "Ошибка сервера")
            return

        if message:
            message_html = f'<div class="message {message_type}">{self.escape_html(message)}</div>'
        else:
            message_html = ''

        html_content = html_template.replace('{{file_list}}', file_list_html)
        html_content = html_content.replace('{{message_block}}', message_html)

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html_content.encode('utf-8'))

    def handle_upload(self):
        content_type = self.headers.get('Content-Type')
        if not content_type:
            self.send_error(400, "Отсутствует заголовок Content-Type")
            return

        if not content_type.startswith('multipart/form-data'):
            self.send_error(400, "Content-Type должен быть multipart/form-data")
            return

        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self.send_error(400, "Пустой запрос")
            return

        try:
            form = parse_multipart(content_type, self.rfile, content_length)
            logging.info("Данные формы успешно распарсены.")
        except Exception as e:
            logging.error(f"Не удалось распарсить данные формы: {e}")
            self.send_error(400, f"Не удалось распарсить данные формы: {e}")
            return

        if 'file' not in form:
            self.send_error(400, "Отсутствует поле 'file'")
            return

        file_field = form['file']
        if not file_field.get('filename'):
            self.send_error(400, "Файл не выбран")
            return

        filename = sanitize_filename(file_field['filename'])
        file_path = os.path.join(UPLOAD_DIR, filename)

        try:
            with open(file_path, 'wb') as f:
                f.write(file_field['content'])
            logging.info(f"Файл {filename} успешно сохранён локально.")
        except Exception as e:
            logging.error(f"Не удалось сохранить файл: {e}")
            self.send_error(500, f"Не удалось сохранить файл: {e}")
            return

        yandex_disk_path = f"disk:/{filename}"
        success = upload_file_to_yandex_disk(YANDEX_DISK_TOKEN, file_path, yandex_disk_path)

        if success:
            try:
                os.remove(file_path)
                logging.info(f"Локальный файл {filename} успешно удалён после загрузки.")
            except Exception as e:
                logging.error(f"Не удалось удалить локальный файл {filename}: {e}")
            message = urllib.parse.quote('Файл успешно загружен')
            self.send_response(303)
            self.send_header('Location', f'/?message={message}&type=success')
            self.end_headers()
        else:
            message = urllib.parse.quote('Ошибка при загрузке файла')
            self.send_response(303)
            self.send_header('Location', f'/?message={message}&type=error')
            self.end_headers()

    def escape_html(self, text):
        return html.escape(text)

def run(server_class=ThreadingHTTPServer, handler_class=SimpleHTTPRequestHandler, port=8000):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f"Сервер запущен на порту {port}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logging.info("Сервер остановлен вручную.")
    except Exception as e:
        logging.critical(f"Ошибка сервера: {e}")
    finally:
        httpd.server_close()
        logging.info("Сервер закрыт.")

if __name__ == '__main__':
    run()
