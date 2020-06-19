#!/usr/bin/env python
# -*- coding: utf-8 -*-

# *****************************************************************************
# МОДУЛЬ:    -
# ФАЙЛ:      SMARTHOMEONE.PY
# ЗАГОЛОВОК: ФАЙЛ ПРОГРАММЫ
# ОПИСАНИЕ:  -
# *****************************************************************************

import os
import sys
import cv2
import yadisk
import telebot
import subprocess
import configparser
from enum import Enum
from time import sleep
from imutils.video import FPS
from datetime import datetime as dt
from threading import Thread, RLock
from gpiozero import LED, MotionSensor

# -----------------------------------------------------------------------------
# SYSTEM
class LedState(Enum):
	ON = 1
	OFF = 2
	BLINK = 3
	BLINK_LONG = 4

# -----------------------------------------------------------------------------
# SYSTEM
class Config:
	"""Provide easy access to project values.\n
	IN: inifile:str - ini filename\n
	IN: errorLog:(ErrorLog) - log object\n
	OUT: self.ok=True, if all values have been read successfuly, False otherwise."""
	def __init__(self, inifile:str, errorLog):
		self.ok = True
		try:
			# значения из файла INI 
			cfg = configparser.ConfigParser()
			cfg.read(inifile)
			self.telegram_token = cfg['TELEGRAM']['token'].strip()
			self.telegram_allowed = list(map(int, [x for x in cfg['TELEGRAM']['allowed'].split(',') if x!='']))
			self.telegram_informed = list(map(int, [x for x in cfg['TELEGRAM']['informed'].split(',') if x!='']))
			
			self.yandex_token = cfg['YANDEX']['token'].strip()
			self.yandex_folder = cfg['YANDEX']['folder'].replace('"', '').strip()
			
			self.camera_device = int(cfg['CAMERA']['device'])
			self.camera_image_frame_width = int(cfg['CAMERA']['image_frame_width'])
			self.camera_image_frame_height = int(cfg['CAMERA']['image_frame_height'])
			self.camera_video_frame_width = int(cfg['CAMERA']['video_frame_width'])
			self.camera_video_frame_height = int(cfg['CAMERA']['video_frame_height'])
			self.camera_saveImagesInterval = int(cfg['CAMERA']['video_saveImagesInterval'])
			self.camera_singlevideo_duration = int(cfg['CAMERA']['video_single_duration'])
			self.camera_alarmvideo_duration = int(cfg['CAMERA']['video_alarm_duration'])

			self.alarm_hysteresis = int(cfg['ALARM']['alarm_hysteresis'])

			# объекты
			self.YaDisk = None # объект Я-Диска
			self.lock = RLock() # блокировщик потоков
			self.PIN_HEALTH = None # пин индикатора HEALTH
			self.PIN_MDETECTOR = None # пин входа от датчика тревоги
			self.log = errorLog # объект журнала событий

			# глобальные переменные
			self.images = list() # список фоток для сохранения на я-диске
			self.camera_is_busy = False # флаг показывающий, что камера занята
			self.camera_FPS = 10 # измеренное FPS
			self.alarm_detected = False # устанавливается в True, когда получен сигнал тревоги
			self.alarm_last = dt(1900, 1, 1) # время последнего срабатывания тревоги
			self.system_staruptime = dt.now() # время запуска системы

		except Exception as ex:
			self.err.write(str(ex))
			self.ok = False


# -----------------------------------------------------------------------------
# SYSTEM
class ErrorLog:
	"""Error log file. The file is cleared when the class initialized."""
	def __init__(self, filename:str):
		self.filename = filename
		with open(self.filename, 'w') as f:
			f.write('')

	def write(self, message:str):
		"""Write text message to log file with timestamp.\n
			IN: message: str - message to write\n
			OUT: -"""
		with open(self.filename, 'a') as f:
			f.write(f"{dt.now()} {message}\n")


# -----------------------------------------------------------------------------
# SYSTEM
def checkTempFolder():
	"""On run check the TEMP folder. If folder contain not uploaded files - add them to queue.\n
		IN: -\n
		OUT: -
	"""
	global cfg
	cfg.images = [f for f in os.listdir("temp") if os.path.isfile(os.path.join("temp", f))]


# -----------------------------------------------------------------------------
# HARDWARE
def led_HEALTH(state:LedState):
	"""Turn the HEALTH indicator ON or OFF.\n
		IN: state: bool - 0-Turn off, 1-Turn on\n
		OUT: -
	"""
	global cfg
	if state == LedState.ON:
		cfg.PIN_HEALTH.on()
	elif state == LedState.OFF:
		cfg.PIN_HEALTH.off()
	elif state == LedState.BLINK:
		cfg.PIN_HEALTH.blink(on_time=0.5, off_time=0.5)
	elif state == LedState.BLINK_LONG:
		cfg.PIN_HEALTH.blink(on_time=1, off_time=1)


# -----------------------------------------------------------------------------
# YANDEX DISK
def connectYaDisk():
	"""Trying to connect to Yandex Disk and create folder if not exist.\n
	   IN: -\n
	   OUT: True if connection was successed, False if not."""
	global cfg
	try:
		cfg.YaDisk = yadisk.YaDisk(token=cfg.yandex_token)
		if (cfg.YaDisk.check_token()):
			if not cfg.YaDisk.exists(cfg.yandex_folder):
				cfg.YaDisk.mkdir(cfg.yandex_folder)
		return cfg.YaDisk.check_token()
	except Exception as ex:
		self.err.write(str(ex))
		return False


# -----------------------------------------------------------------------------
# YANDEX DISK
def saveToCloud():
	"""Save images from TEMP folder to Y-Disk. Must be run in separate thread forever as daemon.\n
		IN: -\n
		OUT: -
	"""
	global cfg
	image = None
	while True:
		sleep(1) # если нет фото в очереди, ждем 1 секунду чтобы не нагружать CPU
		while True: # эмулируем do..while цикл для постоянной отправки фото из очереди
			with cfg.lock:
					if len(cfg.images) > 0:
						image = cfg.images.pop()
					else:
						break
			try:
				if image:
					if os.path.exists(f"temp/{image}"):
						# проверим есть ли папка, если нет - создадим
						folder, fname = image.split('-')
						if not cfg.YaDisk.exists(f"{cfg.yandex_folder}/{folder}"):
										cfg.YaDisk.mkdir(f"{cfg.yandex_folder}/{folder}")
						# сохраним файл в облаке и удалим локально
						cfg.YaDisk.upload(f"temp/{image}", f'{cfg.yandex_folder}/{folder}/{fname}', overwrite=True)
						os.remove(f"temp/{image}")
			finally:
				image = None
			

# -----------------------------------------------------------------------------
# TELEGRAM
def telegram_start(msg):
	"""Show basic info include inputs state.\n
	IN: msg - Telegram message object\n
	OUT: -"""
	global cfg
	user = msg.from_user
	answer = """Hello {uname1} {uname2}{uname3}!
Welcome to Smart Home One System.
Your ID:{uid} is granted for access.
Current system time is: {tcurr}
System works since: {tstart}
Last alarm was at: {alast}
Alarm input state: {ain}
Camera FPS: {cfps}
"""\
		.format(uid=user.id,
		uname1=(user.first_name if user.first_name else ''),
		uname2=(user.last_name if user.last_name else ''),
		uname3=(f" aka {user.username}" if user.username else ''),
		tcurr=dt.now().strftime("%Y-%m-%d %H:%M:%S"),
		tstart=cfg.system_staruptime.strftime("%Y-%m-%d %H:%M:%S"),
		ain=("ON" if cfg.PIN_MDETECTOR.value else "OFF"),
		cfps=int(cfg.camera_FPS),
		alast=('-' if cfg.alarm_last.year==1900 else cfg.alarm_last.strftime("%Y-%m-%d %H:%M:%S")))
	bot.send_message(msg.chat.id, answer)


# -----------------------------------------------------------------------------
# TELEGRAM
def telegram_myip(msg):
	"""Show external IP address.\n
	IN: msg - Telegram message object\n
	OUT: -"""
	try:
		out = subprocess.Popen(['dig', '+short', 'myip.opendns.com', '@resolver1.opendns.com'], 
								stdout=subprocess.PIPE, 
								stderr=subprocess.STDOUT)
		stdout,stderr = out.communicate()
	except Exception as ex:
		self.err.write(str(ex))
		stdout = b"Error."
	bot.send_message(msg.chat.id, f"Your external IP: {stdout.decode()}")


# -----------------------------------------------------------------------------
# TELEGRAM
def telegram_shot(msg):
	"""Make a photo and send with Telegram.\n
	IN: msg - Telegram message object\n
	OUT: -"""
	global cfg

	# проверим занята ли камера, если да, то ждем, но не более 2 минут
	timeout = 120
	while timeout > 0:
		with cfg.lock:
			if not cfg.camera_is_busy:
				break
		sleep(1)
		timeout -= 1

	if timeout == 0:
		# был выход по таймуту
		bot.send_message(msg.chat.id, "Failed to take photo. Camera is busy.")
		return

	filename = 'single.jpg'
	err = False
	try:
		# заблокируем камеру для других процессов
		with cfg.lock:
			cfg.camera_is_busy = True

		cam = cv2.VideoCapture(cfg.camera_device) #, cv2.CAP_DSHOW - добавить только в Windows
		cam.set(cv2.CAP_PROP_FRAME_WIDTH , cfg.camera_image_frame_width)
		cam.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.camera_image_frame_height)

		ret, frame = cam.read()
		if ret:
			cv2.imwrite(filename, frame)
		else:
			err = True	

		cam.release()
		cv2.destroyAllWindows()
	except Exception as ex:
		self.err.write(str(ex))
		err = True
	finally:
		# разблокируем камеру
		with cfg.lock:
			cfg.camera_is_busy = False

	# отправим фото
	with open(filename, 'rb') as f:
		if f:
			bot.send_photo(chat_id=msg.chat.id, photo=f, caption=f'{dt.now()}')
			os.remove(filename)
		else:
			err = True

	if err:
		bot.send_message(msg.chat.id, "Failed to take photo.")


# -----------------------------------------------------------------------------
# TELEGRAM
def telegram_video(msg, single:bool, testFPS:bool=False):
	"""Record video.\n
	IN: msg - Telegram message object\n
	IN: single:bool - if True - record short video and send with Telegram. If False-send with YDisk\n
	IN: testFPS:bool - if set the 10 seconds video captured and real FPS is measured\n
	OUT: -"""
	global cfg

	if testFPS:
		single = True
		print("Evaluating FPS...")

	if single and not testFPS:
		Thread(target=telegram_message, args=([msg.chat.id], "Recording video, please wait..."),
	       	   daemon=True).start()
	# проверим занята ли камера, если да, то ждем, но не более 2 минут
	timeout = 120
	while timeout > 0:
		with cfg.lock:
			if not cfg.camera_is_busy:
				break
		sleep(1)
		timeout -= 1

	if not testFPS:
		if timeout == 0:
			# был выход по таймуту
			if single:
				bot.send_message(msg.chat.id, "Failed to take video. Camera is busy.")
			else:
				telegram_message(cfg.telegram_informed,
				"Warinig! Alarm was detected but camera is busy.")
			return

	base = "(%s)_%s" % (cfg.camera_device, dt.now().strftime("%Y_%m_%d_%H%M"))
	if single:
		filename = 'single.mp4'
	else:
		filename = f"{base}-video.mp4"
	if testFPS:
		duration = 10 * 7 # 10 seconds at 25 fps
	else:
		duration = cfg.camera_FPS * (cfg.camera_singlevideo_duration if single else cfg.camera_alarmvideo_duration)
	err = False
	snapshotCountdown = 0
	snapshotNo = 0

	try:
		# заблокируем камеру для других процессов
		with cfg.lock:
			cfg.camera_is_busy = True

		cam = cv2.VideoCapture(cfg.camera_device) #, cv2.CAP_DSHOW - добавить только в Windows
		fourcc = cv2.VideoWriter_fourcc(*'mp4v')
		recfile = (filename if single else f"temp/{filename}")
		out = cv2.VideoWriter(recfile, fourcc, cfg.camera_FPS,
		                     (cfg.camera_video_frame_width, cfg.camera_video_frame_height))
	
		if testFPS:
			fps = FPS().start()

		while duration > 0:
			ret, frame = cam.read()
			if ret:
				out.write(frame)
				if not single:
					if snapshotCountdown == 0:
						image = f'{base}-{snapshotNo}.jpg'
						cv2.imwrite(f"temp/{image}", frame)
						cfg.images.append(image)
						snapshotCountdown = cfg.camera_saveImagesInterval * cfg.camera_FPS
						snapshotNo += 1
					else:
						snapshotCountdown -= 1
					
			if testFPS:
				fps.update()

			duration -= 1
			
		if testFPS:
			fps.stop()
			cfg.camera_FPS = fps.fps() + 3
			os.remove(filename)
			print("Expected: {0}s. Actual: {1}s. FPS: {2}"\
				.format(10, fps.elapsed(), fps.fps()))

		out.release()
		cam.release()
		cv2.destroyAllWindows()
	except Exception as ex:
		self.err.write(str(ex))
		err = True
	finally:
		# разблокируем камеру, сбросим тревогу
		with cfg.lock:
			cfg.camera_is_busy = False
			cfg.alarm_detected = False
			led_HEALTH(LedState.ON)

	if not testFPS:
		if single:
			with open(filename, 'rb') as f:
				if not err and f:
					bot.send_video(chat_id=msg.chat.id, data=f, caption=f'{dt.now()}')
					os.remove(filename)
				else:
					err = True
		else:
			cfg.images.append(filename)

		if err:
			if single:
				bot.send_message(msg.chat.id, "Failed to record video.")
			else:
				telegram_message(cfg.telegram_informed,
				"Warinig! Alarm was detected but system cannot record the video.")


# -----------------------------------------------------------------------------
# TELEGRAM
def telegram_reset(msg):
	"""Reboot hardware"""
	bot.send_message(msg.chat.id, "Rebooting in process...")
	os.system("sudo reboot")


# -----------------------------------------------------------------------------
# TELEGRAM
def telegram_help(msg):
	text = """
	/start - to check system status and show welcome message\n
	/myip - show external IP\n
	/shot - take single photo from camera and send via Telegram\n
	/video - record 10-seconds video (mp4) and send via Telegram\n
	/alarm - simulate alarm and get results via Yandex Disk\n
	/reset - restart the hardware\n
	/help - show this help"""
	bot.send_message(msg.chat.id, text)

# -----------------------------------------------------------------------------
# TELEGRAM
def telegram_message(receivers:list, text:str):
	"""Send message to given users.\n
	IN: receivers:list - list of users to send message\n
	IN: text:str - test to be sent\n
	OUT: -"""
	for receiver in receivers:
		bot.send_message(receiver, text)


# -----------------------------------------------------------------------------
# FUNCTIONS
def alarmDetected():
	"""To run video recording in the safe thread"""
	global cfg
	if cfg.alarm_detected:
		return
	
	if ((dt.now() - cfg.alarm_last).seconds/60) < cfg.alarm_hysteresis:
		return

	cfg.alarm_last = dt.now()
	led_HEALTH(LedState.BLINK)
	cfg.alarm_detected = True
	Thread(target=telegram_message, args=(cfg.telegram_informed, 
	           "ATTENTION! BREACH DETECTED!"),
	       	   daemon=True).start()
	Thread(target=telegram_video, args=(None,False), daemon=True).start()

# =============================================================================
# MAIN

# создаем файл регистрации ошибок
log = ErrorLog('errorlog.txt')

# создаем временную директорию (здесь будут хранится фотографии до отправки на Я-Диск)

if not os.path.exists("temp"):
    os.makedirs("temp")

# считываем параметры из INI файла
cfg = Config('smarthomeconfig.ini', log)
if not cfg.ok:
	log.write('Error while reading parameters from INI file.')
	exit()

# настройка пинов Raspberry
cfg.PIN_HEALTH = LED(20)
cfg.PIN_MDETECTOR = MotionSensor(21)

# включаем индикатор
led_HEALTH(LedState.BLINK_LONG)

# проверяем содержимое папки TEMP на предмет не отправленных изображений
checkTempFolder()

# пытаемся подключиться к Яндекс Диску
if not connectYaDisk():
	led_HEALTH(LedState.OFF)
	log.write('Unable to connect to Yandex Disk.')
	exit()

# пытаемся подключиться к Телеграм
try:
	bot = telebot.TeleBot(cfg.telegram_token)
except Exception as ex:
	self.err.write(str(ex))
	led_HEALTH(LedState.OFF)
	log.write('Unable to connect to Telegram.')
	exit()	

# настройка функции запуска по тревоге
cfg.PIN_MDETECTOR.when_motion = alarmDetected

# запускаем поток управления загрузкой на Яндекс Диск
Thread(target=saveToCloud, daemon=True).start()

# подсчитываем FPS
telegram_video(None, True, True)

# запускаем цикл обработки событий
led_HEALTH(LedState.ON)
print("Start processing...")
telegram_message(cfg.telegram_informed, "The system was started")
updateid = 0
while True:
	updates = bot.get_updates(updateid, 1, 3) # таймаут 3 секунды
	if len(updates) > 0:
		msg = updates[0].message
		# проверим доступ
		if (msg.from_user.id in cfg.telegram_allowed) or (len(cfg.telegram_allowed)==0):
			if msg.text and msg.text[0] == '/':
				# обработаем входяшу команду
				text = msg.text.upper()
				if text == '/START':
					Thread(target=telegram_start, args=(msg,), daemon=True).start()
				elif text == '/MYIP':
					Thread(target=telegram_myip, args=(msg,), daemon=True).start()
				elif text == '/SHOT':
					Thread(target=telegram_shot, args=(msg,), daemon=True).start()
				elif text == '/VIDEO':
					Thread(target=telegram_video, args=(msg,True), daemon=True).start()
				elif text == '/ALARM':
					alarmDetected()
				elif text == '/HELP':
					Thread(target=telegram_help, args=(msg,), daemon=True).start()
				elif text == '/RESET':
					Thread(target=telegram_reset, args=(msg,), daemon=True).start()					
		updateid = updates[0].update_id + 1 # увеличим ID на 1 что бы предыдущее считалось обработанным
	else:
		updateid = 0



