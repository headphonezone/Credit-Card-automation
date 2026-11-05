"""Run once a day (Windows Task Scheduler). Sends reminders for unpaid statements only."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import reminders

if __name__ == '__main__':
    dry = '--dry' in sys.argv
    for line in reminders.run_once(send=not dry) or [('nothing to send today',)]:
        print(*line)
