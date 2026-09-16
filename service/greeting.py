from dataclasses import dataclass
from typing import Optional
import math
import os
import re
import subprocess
import time
from service.state import read_json, write_json


DEFAULT_GREETING_COOLDOWN = 600


@dataclass(frozen=True)
class GreetingDecision:
    should_play: bool
    session: str
    previous_session: Optional[str]
    cooldown_active: bool
    reason: str


def startup_greeting(user_title='сэр', hour=None, time_aware=False):
    # Exact default is intentional; time-of-day variants are opt-in for future use.
    salutation = 'Здравствуйте'
    if time_aware and hour is not None:
        if 5 <= hour < 12:
            salutation = 'Доброе утро'
        elif 18 <= hour < 24:
            salutation = 'Добрый вечер'
    return f'{salutation}, {user_title}. JARVIS готов к работе.'


def current_login_session_id(runner=subprocess.run):
    boot = 'unknown-boot'
    try:
        result = runner(['/usr/sbin/sysctl', '-n', 'kern.boottime'], shell=False, capture_output=True,
                        text=True, timeout=2)
        if result.returncode == 0:
            match = re.search(r'sec\s*=\s*(\d+)', result.stdout)
            if match:
                boot = match.group(1)
    except Exception:
        pass
    return f'boot={boot};sid={os.getsid(0)}'


def greeting_decision(path, session=None, now=None, cooldown=DEFAULT_GREETING_COOLDOWN):
    now = time.time() if now is None else now
    session = current_login_session_id() if session is None else session
    state = read_json(path)
    previous_session = state.get('last_greeting_session')
    last = state.get('last_greeting_time')
    previous = previous_session if isinstance(previous_session, str) else None
    cooldown_active = (previous == session and type(last) in (int, float) and
                       math.isfinite(last) and 0 <= now - last < cooldown)
    if cooldown_active:
        return GreetingDecision(False, session, previous, True, 'cooldown active in same login session')
    if previous != session:
        return GreetingDecision(True, session, previous, False, 'new login session')
    return GreetingDecision(True, session, previous, False, 'cooldown expired')


def record_greeting(path, session=None, now=None):
    now = time.time() if now is None else now
    session = current_login_session_id() if session is None else session
    write_json(path, {'last_greeting_time': now, 'last_greeting_session': session})


def reserve_greeting(path, now=None, cooldown=DEFAULT_GREETING_COOLDOWN, session=None):
    decision = greeting_decision(path, session=session, now=now, cooldown=cooldown)
    if decision.should_play:
        record_greeting(path, session=decision.session, now=now)
    return decision.should_play
