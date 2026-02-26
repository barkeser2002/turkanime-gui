from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import traceback

binary = r"C:\Users\bkese\.turkanime_chromium\1590396\chrome-win\chrome.exe"
print('Using binary:', binary)
opts = Options()
opts.binary_location = binary
try:
    d = webdriver.Chrome(options=opts)
    print('Started, browserName=', d.capabilities.get('browserName'))
    d.quit()
except Exception as e:
    traceback.print_exc()
    print('FAILED:', e)
