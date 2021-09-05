from PIL import ImageFont
from luma.core.render import canvas
import threading
from demo_opts import get_device
from datetime import datetime
from pathlib import Path
import time
import RPi.GPIO as GPIO
import pcf8574_io
import ADS1115
from ina219 import INA219
from ina219 import DeviceRangeError
import serial
# import oled
import os
import sys
from serial.tools import list_ports
import subprocess
import radio_test

p1 = pcf8574_io.PCF(0x20)
ads = ADS1115.ADS1115()
# display = oled.SSD1327(bus=1)

cp2102_id = ['10C4', 'EA60']
uart_busy = False

product = ['SPI_868', 'SPI_915', 'USB_868', 'USB_915']
##########product id##############
#0   868 spi
#1   915 spi
#2   usb_868
#3   usb_915
############################display########################

if os.name != 'posix':
	sys.exit('{} platform not supported'.format(os.name))


try:
	import psutil
except ImportError:
	print("The psutil library was not found. Run 'sudo -H pip install psutil' to install it.")
	sys.exit()
words_type = None
device = None
all_result = []


###########################################################
used_freq_868_up = [867.1, 867.3, 867.5, 867.7, 867.9, 868.1, 868.3, 868.5]
used_freq_868_down = [867.1, 867.3, 867.5, 867.7, 867.9, 868.1, 868.3, 868.5]
used_freq_915_up = [903.9, 904.1, 904.3, 904.5, 904.7, 904.9, 905.1, 905.3]
used_freq_915_down = [923.3, 923.9, 924.5, 925.1, 925.7, 926.3, 926.9, 927.5]

used_freq_up = 0
used_freq_down = 0
send_test_freq = 0
receive_test_freq = 0

Firmware_button=5
RECEIVE_TEST_BUTTON = 26
SEND_TEST_BUTTON = 21
OTHER_TEST_BUTTON = 24
RST_PIN = 25
current_ref = 0
run_times = 0
usb_type = False

max_current = 0
min_current = 1000
sum_current = 0
average_current = 0
get_current_times = 0
keep_scan = False


def get_port(id):
	timeout = 4
	com_key1 = 'FTDIBUS\\VID_'+id[0]+'+PID_'+id[1]
	com_key2 = 'USB VID:PID='+id[0]+':'+id[1]
	while timeout != 0:
		port1 = None
		for p in list_ports.comports():
			if p[2].upper().startswith(com_key1) or p[2].upper().startswith(com_key2):
				port1 = p[0]
				# print(port1)
				return port1
		time.sleep(0.1)
		timeout -= 1
	print('No port is found')
	return None


cp2102_ser = serial.Serial(port=get_port(cp2102_id),
						   baudrate=9600,
						   bytesize=8,
						   stopbits=1,
						   timeout=0.1)


def get_product_id():
	for i in range(4):
		pin = 'p'+str(i)
		p1.pin_mode(pin, "INPUT")
	id = 1
	for n in range(4):
		id <<= 1
		pin = 'p'+str(n)
		if p1.digital_read(pin):
			id += 1
	id -= 16
	print('product id :', id)
	return id


def get_test_equipment_id():
	for i in range(4, 8):
		pin = 'p'+str(i)
		p1.pin_mode(pin, "INPUT")
	id = 1
	for n in range(4, 8):
		id <<= 1
		pin = 'p'+str(n)
		if p1.digital_read(pin):
			id += 1
	id -= 16
	print('equipment_id: ', id)
	return id


def get_vol(channel):
	volt = ads.readADCSingleEnded(channel)
	return volt*6


def get_current():
	global max_current, min_current, sum_current, average_current, get_current_times
	SHUNT_OHMS = 0.1
	ina = INA219(SHUNT_OHMS)
	ina.configure()
	current = ina.current()
	if current <= 5:
		return
	get_current_times += 1
	# max_current = max(current, max_current)
	# min_current = min(current, min_current)
	sum_current += current
	average_current = sum_current/get_current_times

	# print("Bus Voltage: %.3f V" % ina.voltage())
	# try:
	#     print("Bus Current: %.3f mA" % (current))
	#     print("Power: %.3f mW" % ina.power())
	#     print("Shunt voltage: %.3f mV" % ina.shunt_voltage())
	# except DeviceRangeError as e:
	#     # Current out of device range with specified shunt resistor
	#     print(e)
	# return current


def keep_scan_current():
	global keep_scan
	time.sleep(1)
	while keep_scan:
		get_current()
		time.sleep(0.1)


def uart_cmd(CMD, lines, key):
	global uart_busy
	while uart_busy:
		time.sleep(0.2)
	uart_busy = True
	result = []
	check_result = False
	data = cp2102_ser.read(1024)
	# print(data)
	cp2102_ser.write(CMD.encode('utf-8'))
	for i in range(lines):
		data = cp2102_ser.readline()
		try:
			line = data.decode('utf-8')
			if len(line) >= 4:
				line = line.strip()
				print(line)
				result.append(line)
				if key in line:
					check_result = True
					break
				else:
					pass
		except:
			pass
	if check_result == False:
		print(CMD[:-3], '     Failed')
	uart_busy = False
	return result, check_result


def lora_slave_init():
	for i in range(3):
		result1, check_result1 = uart_cmd('AT+MODE=TEST\r\n', 10, '+MODE:')
		if check_result1:
			print('lora slave init over')
			return check_result1
		else:
			time.sleep(0.5)
	print('lora slave init fail')
	return False


def lora_slave_set_freq(freq):
	cmd = 'AT+TEST=RFCFG,%f,SF12,125,8,8,10,ON,OFF,ON\r\n' % (freq)
	for n in range(3):
		result2, check_result2 = uart_cmd(cmd, 10, '+TEST:')
		if check_result2:
			print('lora slave set freq over')
			return check_result2
		else:
			time.sleep(0.5)
	print('lora slave set freq fail')
	return False


def init_gpio():
	global RECEIVE_TEST_BUTTON, SEND_TEST_BUTTON, OTHER_TEST_BUTTON, usb_type, RST_PIN,Firmware_button
	GPIO.setmode(GPIO.BCM)
	GPIO.setwarnings(False)
	GPIO.setup(RECEIVE_TEST_BUTTON, GPIO.OUT)
	GPIO.output(RECEIVE_TEST_BUTTON, 1)
	GPIO.setup(SEND_TEST_BUTTON, GPIO.OUT)
	GPIO.output(SEND_TEST_BUTTON, 1)
	GPIO.setup(OTHER_TEST_BUTTON, GPIO.OUT)
	GPIO.output(OTHER_TEST_BUTTON, 1)
	GPIO.setup(RST_PIN, GPIO.OUT)
	GPIO.setup(Firmware_button, GPIO.IN,pull_up_down=GPIO.PUD_UP)
	if usb_type:
		GPIO.output(RST_PIN, 1)
	else:
		GPIO.output(RST_PIN, 0)


def enable_freq_used():
	result, checkresult = uart_cmd('AT+TEST=TXCW\r\n', 10, '+TEST:')
	return checkresult


def disable_freq_used():
	result, checkresult = uart_cmd('AT+TEST=STOP\r\n', 10, '+TEST:')
	return checkresult


def get_push(gpio):
	result = subprocess.check_output(
		['raspi-gpio', 'get', str(gpio)]).decode('utf-8')
	# print(result)
	if 'level=0' in result:
		return True
	else:
		return False


def current_test():
	print('')
	print('')
	print('**************************current_test*************************************')
	print('')
	print('')
	global current_ref, average_current, keep_scan, usb_type
	# current = get_current()
	diff = abs((current_ref-average_current)*100)/current_ref
	print('target_current: ', average_current, '     current_ref: ',
		  current_ref, '     diff: ', diff, '%')
	keep_scan = False
	if diff <= 30:
		return 'CURRENT', True
	else:
		return 'CURRENT', False


def vol_test(channel, vol_ref):
	vol = get_vol(channel)
	diff = abs((vol-vol_ref)*100)/vol_ref
	print('target_vol: ', vol, '     vol_ref: ',
		  vol_ref, '     diff: ', diff, '%')
	if diff <= 5:
		return True
	else:
		return False


def test_3v3():
	print('')
	print('')
	print('**************************test_3v3*************************************')
	print('')
	print('')
	return 'V3V3', vol_test(3, 3300)


def test_1v2():
	print('')
	print('')
	print('**************************test_1v2*************************************')
	print('')
	print('')
	return 'V1V2', vol_test(0, 1200)


def test_spi_connect():
	global usb_type
	print('')
	print('')
	print('**************************test_spi_connect*************************************')
	print('')
	print('')
	return 'SPI', radio_test.check_module_connect(usb=usb_type)


def lbt_test():
	global used_freq_up, used_freq_down, usb_type
	print('')
	print('')
	print('**************************lbt_test*************************************')
	print('')
	print('')
	lora_slave_set_freq(used_freq_down)
	enable_freq_used()
	result1 = radio_test.check_module_lbt(used_freq_down, usb=usb_type)
	if result1 == True:
		return 'LBT', False
	disable_freq_used()
	result2 = radio_test.check_module_lbt(used_freq_down, usb=usb_type)
	return 'LBT', result2


def pps_test():
	print('')
	print('')
	print('**************************pps_test*************************************')
	print('')
	print('')
	global usb_type
	result = radio_test.check_module_pps(usb=usb_type)
	return 'PPS', result


def rst_test():
	print('')
	print('')
	print('**************************rst_test*************************************')
	print('')
	print('')
	global usb_type
	result = radio_test.check_module_rst(usb=usb_type)
	return 'RST', result


def send_test():
	global send_test_freq, SEND_TEST_BUTTON, usb_type
	print('')
	print('')
	print('**************************send_test*************************************')
	print('')
	print('')
	GPIO.output(SEND_TEST_BUTTON, 0)
	time.sleep(0.2)
	result = radio_test.check_module_tx(
		send_test_freq, usb=usb_type, timeout=7.0)
	GPIO.output(SEND_TEST_BUTTON, 1)


def receive_test():
	global receive_test_freq, RECEIVE_TEST_BUTTON, usb_type, sum_current, average_current, get_current_times, keep_scan
	print('')
	print('')
	print('**************************receive_test*************************************')
	print('')
	print('')
	sum_current = 0
	average_current = 0
	get_current_times = 0
	keep_scan = True
	GPIO.output(RECEIVE_TEST_BUTTON, 0)
	time.sleep(0.2)
	th = threading.Thread(target=keep_scan_current)
	th.start()
	result = radio_test.check_module_rx(receive_test_freq,  usb=usb_type)
	GPIO.output(RECEIVE_TEST_BUTTON, 1)
	show_result(current_test())
	show_result(['RX', result])


def other_test():
	global OTHER_TEST_BUTTON, run_times, sum_current, average_current, get_current_times, keep_scan,Firmware_button
	print('')
	print('')
	print('**************************other_test*************************************')
	print('')
	print('')
	sum_current = 0
	average_current = 0
	get_current_times = 0
	keep_scan = True
	while True:
		title, result = test_3v3()
		lev=GPIO.input(Firmware_button)
		if result and lev:
			run_times = time.time()
			# time.sleep(1)
			# th = threading.Thread(target=keep_scan_current)
			# th.start()
			GPIO.output(OTHER_TEST_BUTTON, 0)
			show_result([title, result])
			show_result(test_1v2())
			# show_result(current_test())
			show_result(test_spi_connect())
			show_result(lbt_test())
			show_result(pps_test())
			show_result(rst_test())
			GPIO.output(OTHER_TEST_BUTTON, 1)
			break
		else:
			time.sleep(0.5)
			print('wait to connect spi')


def show_result(data):
	global words_type, device, all_result
	all_result.append(data)
	show_line = 0
	# print(all_result)
	with canvas(device) as draw:
		for i in all_result:
			title = i[0]
			result = i[1]
			draw.text((0, show_line), title, font=words_type, fill="white")
			if result == True:
				draw.text((110, show_line), 'OK',
						  font=words_type, fill="white")
			elif result == False:
				draw.text((110, show_line), 'NG',
						  font=words_type, fill="white")
			else:
				pass
			show_line += 13


def display_init():
	global words_type, device
	device = get_device()
	font_path = str(Path(__file__).resolve().parent.joinpath(
		'fonts', 'C&C Red Alert [INET].ttf'))
	words_type = ImageFont.truetype(font_path, 18)


def test_init():
	print('')
	print('')
	print('**************************test_init*************************************')
	print('')
	print('')
	global used_freq_down, used_freq_up, send_test_freq, receive_test_freq, current_ref, product, usb_type
	test_equipment_id = get_test_equipment_id()
	product_id = get_product_id()
	if product_id % 2 == 0:
		used_freq_up = used_freq_868_up[test_equipment_id]
		used_freq_down = used_freq_868_down[test_equipment_id]
		send_test_freq = 867.7
		receive_test_freq = 867.7
		current_ref = 14
	elif product_id % 2 == 1:
		used_freq_up = used_freq_915_up[test_equipment_id]
		used_freq_down = used_freq_915_down[test_equipment_id]
		send_test_freq = 925.1
		receive_test_freq = 904.5
		current_ref = 14
	print('used_freq_up: ', used_freq_up)
	print('used_freq_down: ', used_freq_down)
	print('send_test_freq: ', send_test_freq)
	print('receive_test_freq: ', receive_test_freq)
	lora_slave_init()
	equipment = 'E_id:        '+str(test_equipment_id)
	send_freq = 'S_freq:     '+str(send_test_freq)
	receive_freq = 'R_freq:     '+str(send_test_freq)
	used_up = 'U_up:       '+str(used_freq_up)
	used_down = 'U_down:    '+str(used_freq_down)
	if 'USB' in product[product_id]:
		usb_type = True
		current_ref = 23
	init_gpio()
	display_init()
	show_result([product[product_id], None])
	show_result([equipment, None])
	show_result([send_freq, None])
	show_result([receive_freq, None])
	show_result([used_up, None])
	show_result([used_down, None])
	# time.sleep(1)


def test_all():
	global all_result, run_times
	all_result = []
	other_test()
	receive_test()
	send_test()
	cost_times = 'COST_TIME:    '+str(int(time.time()-run_times))+' S'
	show_result([cost_times, None])


test_init()
time.sleep(1)
while True:
	test_all()
	result = True
	while result:
		title, result = test_3v3()
		time.sleep(0.5)
	if usb_type:
		GPIO.output(RST_PIN, 1)
	else:
		GPIO.output(RST_PIN, 0)


#### sudo python3 gx1302_test.py --display sh1106 --height 128 --rotate 2 --interface i2c


xxx
