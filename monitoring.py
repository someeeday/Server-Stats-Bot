import asyncio
from datetime import datetime, timedelta
import logging
import paramiko
from typing import Dict, Any
import time

# Пороговые значения для оповещений
THRESHOLDS = {
    'cpu': 80,  # CPU usage above 80%
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

class SystemMonitor:
    def __init__(self, bot, check_interval=300):  # 300 секунд = 5 минут
        self.bot = bot
        self.check_interval = check_interval
        self.monitoring_tasks = {}  # user_id: task
        self.logger = logging.getLogger("server-stats-bot.monitor")
        self.ssh_pool = SSHPool()
        self.metrics_cache = MetricsCache()
        
        # Оптимизированные команды для Linux
        self.linux_commands = {
            'cpu': "top -bn1 | grep 'Cpu(s)' | awk '{print int($2)}'",
            'ram': "free | grep Mem | awk '{print int($3/$2 * 100)}'",
            'disk': "df / | tail -1 | awk '{print int($5)}'"
        }
        
        # Оптимизированные команды для Windows
        self.windows_commands = {
            'cpu': 'powershell -command "$cpu=(Get-Counter \'\Processor(_Total)\% Processor Time\').CounterSamples.CookedValue;Write-Output ([Math]::Round($cpu))"',
            'ram': 'powershell -command "$os=Get-WmiObject Win32_OperatingSystem;$used=$os.TotalVisibleMemorySize-$os.FreePhysicalMemory;Write-Output ([Math]::Round($used/$os.TotalVisibleMemorySize*100))"',
            'disk': 'powershell -command "$disk=Get-PSDrive C;Write-Output ([Math]::Round($disk.Used/($disk.Used+$disk.Free)*100))"'
        }

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
            
            # Определяем ОС и выбираем команды
            os_type = "windows" if self._is_windows(ssh) else "linux"
            commands = self.windows_commands if os_type == "windows" else self.linux_commands
            
            # Выполняем все команды сразу
            metrics = {}
            for metric, cmd in commands.items():
                try:
                    _, stdout, _ = ssh.exec_command(cmd, timeout=5)
                    value = float(stdout.read().decode().strip())
                    metrics[metric] = value
                except:
                    metrics[metric] = 0
            
            # Кэшируем результат
            self.metrics_cache.set(user_id, metrics)
            return metrics
            
        except Exception as e:
            self.logger.error(f"Ошибка при получении метрик: {e}")
            return {'cpu': 0, 'ram': 0, 'disk': 0}

    def _is_windows(self, ssh: paramiko.SSHClient) -> bool:
        try:
            _, stdout, _ = ssh.exec_command('ver', timeout=2)
            return 'windows' in stdout.read().decode().lower()
        except:
            return False

    async def _check_thresholds(self, user_id: int, metrics: dict):
        alerts = []
        
        for resource, value in metrics.items():
            if value >= THRESHOLDS[resource]:
                alerts.append((resource, value))
                
        if alerts:
            message = "⚠️ *Критические показатели:*\n\n"
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
