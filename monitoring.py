import asyncio
from datetime import datetime, timedelta
import logging
import paramiko # type: ignore
from typing import Dict, Any
import time
import json
from main import logger  # Используем logger из main

# Пороговые значения для оповещений
THRESHOLDS = {
    'cpu': 90,  # CPU usage above 80%
    'ram': 90,  # RAM usage above 85%
    'disk': 90  # Disk usage above 90%
}

# Рекомендации при превышении порогов
RECOMMENDATIONS = {
    'cpu': [
        "• Проверьте запущенные процессы командой `top` или `htop`",
        "• Завершите неиспользуемые процессы",
        "• Рассмотрите возможность обновления CPU или распределения нагрузки"
    ],
    'ram': [
        "• Очистите кэш и временные файлы",
        "• Проверьте процессы, потребляющие много памяти",
        "• Рассмотрите возможность добавления RAM или включения swap"
    ],
    'disk': [
        "• Удалите ненужные файлы и очистите корзину",
        "• Проверьте самые большие файлы командой `du -h`",
        "• Рассмотрите возможность расширения диска"
    ]
}

class SSHPool:
    def __init__(self):
        self.connections: Dict[int, paramiko.SSHClient] = {}
        self.last_used: Dict[int, float] = {}
        self.timeout = 300  # 5 минут неактивности до закрытия
        
    def get_connection(self, user_id: int, ssh_data: dict) -> paramiko.SSHClient:
        current_time = time.time()
        
        # Очистка старых соединений
        self._cleanup(current_time)
        
        if user_id in self.connections:
            self.last_used[user_id] = current_time
            return self.connections[user_id]
            
        # Создание нового соединения
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=ssh_data['hostname'],
            username=ssh_data['username'],
            password=ssh_data['password'],
            port=ssh_data.get('port', 22),
            timeout=5,  # Уменьшаем таймаут
            banner_timeout=5
        )
        
        self.connections[user_id] = client
        self.last_used[user_id] = current_time
        return client
        
    def _cleanup(self, current_time: float):
        for user_id in list(self.connections.keys()):
            if current_time - self.last_used[user_id] > self.timeout:
                self.close_connection(user_id)
                
    def close_connection(self, user_id: int):
        if user_id in self.connections:
            try:
                self.connections[user_id].close()
            except:
                pass
            del self.connections[user_id]
            del self.last_used[user_id]

class MetricsCache:
    def __init__(self, ttl: int = 30):  # Кэш на 30 секунд
        self.cache: Dict[int, Dict[str, Any]] = {}
        self.ttl = ttl
        self.last_update: Dict[int, float] = {}
        
    def get(self, user_id: int) -> Dict[str, Any] | None:
        if user_id in self.cache:
            if time.time() - self.last_update[user_id] < self.ttl:
                return self.cache[user_id]
        return None
        
    def set(self, user_id: int, data: Dict[str, Any]):
        self.cache[user_id] = data
        self.last_update[user_id] = time.time()

def format_size(size: float, unit: str) -> str:
    """Форматирует размер в читаемый вид"""
    if unit.upper() == 'MB':
        if size > 1024:
            return f"{size/1024:.1f} GB"
        return f"{size:.0f} MB"
    elif unit.upper() == 'GB':
        if size > 1024:
            return f"{size/1024:.1f} TB"
        return f"{size:.1f} GB"
    return f"{size} {unit}"

class SystemMonitor:
    def __init__(self, bot, check_interval=300):  # 300 секунд = 5 минут
        self.bot = bot
        self.check_interval = check_interval
        self.monitoring_tasks = {}  # user_id: task
        self.logger = logging.getLogger("server-stats-bot.monitor")
        self.ssh_pool = SSHPool()
        self.metrics_cache = MetricsCache()
        self.alert_states = {}  # Словарь для хранения состояний алертов
        
        # Оптимизированные легкие команды для Linux
        self.linux_commands = {
            'cpu': "cat /proc/stat | head -n1 | awk '{print ($2+$4)*100/($2+$4+$5)}'",  # Более легкий способ
            'ram': "free | awk '/Mem:/ {print int($3/$2 * 100)}'",  # Прямой расчет без промежуточных команд
            'disk': "df -P / | tail -1 | awk '{print int($5)}'"  # Убираем grep и лишние операции
        }
        
        # Оптимизированные легкие команды для Windows
        self.windows_commands = {
            'cpu': 'powershell -command "$cpu=Get-CimInstance Win32_Processor;$cpu.LoadPercentage"',  # Более легкий способ
            'ram': 'powershell -command "Get-CimInstance Win32_OperatingSystem | % {[math]::Round(100-($_.FreePhysicalMemory/$_.TotalVisibleMemorySize*100))}"',
            'disk': 'powershell -command "Get-CimInstance Win32_LogicalDisk -Filter \"DeviceID=\'C:\'\" | % {[math]::Round(100-($_.FreeSpace/$_.Size*100))}"'
        }
        self.last_alert_time = {}  # Добавляем отслеживание времени последнего алерта
        # Добавляем порт для агента
        self.agent_port = 8080

    async def start_monitoring(self, user_id, ssh_data):
        """Запускает мониторинг для конкретного пользователя"""
        if user_id in self.monitoring_tasks:
            return False
        
        # Проверяем возможность подключения сразу
        try:
            metrics = await self._get_metrics(user_id, ssh_data)
            if not metrics:
                return False
        except Exception as e:
            self.logger.error(f"Ошибка при старте мониторинга: {e}")
            return False
            
        task = asyncio.create_task(self._monitor_loop(user_id, ssh_data))
        self.monitoring_tasks[user_id] = task
        self.logger.info(f"Запущен мониторинг для пользователя {user_id}")
        return True

    async def stop_monitoring(self, user_id):
        """Останавливает мониторинг для пользователя"""
        if user_id in self.monitoring_tasks:
            self.monitoring_tasks[user_id].cancel()
            del self.monitoring_tasks[user_id]
            # Очищаем состояния алертов при остановке мониторинга
            if user_id in self.alert_states:
                del self.alert_states[user_id]
            self.logger.info(f"Остановлен мониторинг для пользователя {user_id}")
            return True
        return False

    def is_monitoring(self, user_id):
        """Проверяет, запущен ли мониторинг для пользователя"""
        return user_id in self.monitoring_tasks

    async def _monitor_loop(self, user_id, ssh_data):
        """Цикл мониторинга для конкретного пользователя"""
        try:
            while True:
                try:
                    metrics = await self._get_metrics(user_id, ssh_data)
                    await self._check_thresholds(user_id, metrics)
                except Exception as e:
                    self.logger.error(f"Ошибка в цикле мониторинга: {e}")
                    await self.bot.send_message(
                        user_id,
                        "❌ Ошибка при получении данных мониторинга. Мониторинг остановлен."
                    )
                    break

                await asyncio.sleep(self.check_interval)

        except asyncio.CancelledError:
            self.logger.info(f"Мониторинг отменен для пользователя {user_id}")
        finally:
            self.ssh_pool.close_connection(user_id)
            if user_id in self.monitoring_tasks:
                del self.monitoring_tasks[user_id]

    async def _get_metrics(self, user_id: int, ssh_data: dict) -> dict:
        # Проверяем кэш
        cached_data = self.metrics_cache.get(user_id)
        if cached_data:
            return cached_data
            
        try:
            ssh = self.ssh_pool.get_connection(user_id, ssh_data)
            
            # Пробуем получить метрики от агента через SSH туннель
            try:
                # Создаем SSH туннель до агента
                ssh.get_transport().request_port_forward('', self.agent_port, 'localhost', self.agent_port)
                
                # Выполняем запрос к агенту через туннель
                _, stdout, _ = ssh.exec_command(f'curl -s http://localhost:{self.agent_port}/metrics', timeout=5)
                metrics_data = stdout.read().decode().strip()
                metrics = json.loads(metrics_data)
                
                # Кэшируем результат
                self.metrics_cache.set(user_id, metrics)
                return metrics
                
            except Exception as agent_error:
                logger.warning(f"Не удалось получить метрики от агента: {agent_error}. Используем старый метод.")
                # Если агент недоступен, используем старый метод
                return await self._get_metrics_legacy(ssh)
            
        except Exception as e:
            logger.error(f"Ошибка при получении метрик: {e}")
            return {'cpu': 0, 'ram': 0, 'disk': 0}

    async def _get_metrics_legacy(self, ssh):
        """Оптимизированный метод получения метрик"""
        try:
            os_type = "windows" if self._is_windows(ssh) else "linux"
            commands = self.windows_commands if os_type == "windows" else self.linux_commands
            
            metrics = {}
            # Выполняем команды одновременно через ; для оптимизации
            combined_cmd = " ; ".join(f"echo '{k}='$({v})" for k, v in commands.items())
            
            _, stdout, _ = ssh.exec_command(combined_cmd, timeout=5)
            output = stdout.read().decode().strip()
            
            for line in output.split('\n'):
                if '=' in line:
                    key, value = line.strip().split('=')
                    try:
                        if '|' in value:  # Для RAM и Disk
                            used, total = map(float, value.split('|'))
                            if key == 'ram':
                                metrics[f'{key}_used'] = format_size(used, 'MB')
                                metrics[f'{key}_total'] = format_size(total, 'MB')
                                metrics[key] = round(used / total * 100, 1)
                            else:  # disk
                                metrics[f'{key}_used'] = format_size(used, 'GB')
                                metrics[f'{key}_total'] = format_size(total, 'GB')
                                metrics[key] = round(used / total * 100, 1)
                        else:  # Для CPU
                            metrics[key] = float(value)
                    except ValueError:
                        metrics[key] = 0
            
            return metrics
            
        except Exception as e:
            logger.error(f"Ошибка при получении метрик: {e}")
            return {'cpu': 0, 'ram': 0, 'disk': 0}

    def _is_windows(self, ssh: paramiko.SSHClient) -> bool:
        try:
            _, stdout, _ = ssh.exec_command('ver', timeout=2)
            return 'windows' in stdout.read().decode().lower()
        except:
            return False

    async def _check_thresholds(self, user_id: int, metrics: dict):
        """Оптимизированная проверка метрик"""
        current_time = time.time()
        min_alert_interval = 3600  # 1 час между алертами
        
        if user_id not in self.alert_states:
            self.alert_states[user_id] = {k: False for k in THRESHOLDS.keys()}
            self.last_alert_time[user_id] = {k: 0 for k in THRESHOLDS.keys()}

        alerts_to_send = []
        resolved_alerts = []

        for resource in THRESHOLDS.keys():
            current_value = metrics.get(resource, 0)
            is_critical = current_value >= THRESHOLDS[resource]
            prev_state = self.alert_states[user_id][resource]
            last_alert = self.last_alert_time[user_id][resource]

            if is_critical and (not prev_state or current_time - last_alert >= min_alert_interval):
                details = []
                if resource in ['ram', 'disk']:
                    used = metrics.get(f'{resource}_used', 'N/A')
                    total = metrics.get(f'{resource}_total', 'N/A')
                    details.append(f"Использовано: {used} из {total}")
                alerts_to_send.append((resource, current_value, details))
                self.last_alert_time[user_id][resource] = current_time
            elif not is_critical and prev_state:
                resolved_alerts.append(resource)

            self.alert_states[user_id][resource] = is_critical

        # Отправляем уведомления только если есть что отправлять
        if alerts_to_send:
            message = "⚠️ *Критические показатели:*\n\n"
            for resource, value, details in alerts_to_send:
                message += f"*{self._get_resource_name(resource)}:* {value:.1f}%\n"
                for detail in details:
                    message += f"{detail}\n"
                message += "*Рекомендации:*\n"
                for rec in RECOMMENDATIONS[resource]:
                    message += f"{rec}\n"
                message += "\n"
            
            await self.bot.send_message(user_id, message, parse_mode="Markdown")

        if resolved_alerts:
            message = "✅ *Показатели пришли в норму:*\n\n"
            for resource in resolved_alerts:
                message += f"*{self._get_resource_name(resource)}*\n"
            await self.bot.send_message(user_id, message, parse_mode="Markdown")

    async def _check_metrics(self, user_id, system_data):
        """Проверяет метрики и отправляет уведомления при необходимости"""
        alerts = []
        
        try:
            cpu_usage = float(system_data.get('Загрузка процессора', 0))
            ram_usage = float(system_data.get('Использование ОЗУ', 0))
            disk_usage = float(system_data.get('Использование диска', 0))

            if cpu_usage >= THRESHOLDS['cpu']:
                alerts.append(('cpu', cpu_usage))
            if ram_usage >= THRESHOLDS['ram']:
                alerts.append(('ram', ram_usage))
            if disk_usage >= THRESHOLDS['disk']:
                alerts.append(('disk', disk_usage))

            if alerts:
                message = "⚠️ *Обнаружены критические показатели:*\n\n"
                for resource, value in alerts:
                    message += f"*{self._get_resource_name(resource)}:* {value:.1f}%\n"
                    message += "*Рекомендации:*\n"
                    for rec in RECOMMENDATIONS[resource]:
                        message += f"{rec}\n"
                    message += "\n"

                await self.bot.send_message(
                    user_id,
                    message,
                    parse_mode="Markdown"
                )

        except Exception as e:
            self.logger.error(f"Ошибка при проверке метрик: {e}")

    def _get_resource_name(self, resource):
        """Возвращает человекочитаемое название ресурса"""
        names = {
            'cpu': 'Загрузка процессора',
            'ram': 'Использование памяти',
            'disk': 'Использование диска'
        }
        return names.get(resource, resource)
