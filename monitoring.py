import asyncio
from datetime import datetime, timedelta
import logging
import paramiko # type: ignore
from typing import Dict, Any
import time
import json
from main import logger

THRESHOLDS = {
    'cpu': 90,
    'ram': 90,
    'disk': 90
}

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
        """Инициализация пула SSH-соединений с автоматической очисткой неактивных соединений"""
        self.connections: Dict[int, paramiko.SSHClient] = {}
        self.last_used: Dict[int, float] = {}
        self.timeout = 300
        
    def get_connection(self, user_id: int, ssh_data: dict) -> paramiko.SSHClient:
        """
        Получает существующее или создает новое SSH-соединение.
        
        Args:
            user_id: ID пользователя
            ssh_data: Словарь с параметрами подключения (hostname, username, password, port)
            
        Returns:
            Объект SSH-соединения
        """
        current_time = time.time()
        
        self._cleanup(current_time)
        
        if user_id in self.connections:
            self.last_used[user_id] = current_time
            return self.connections[user_id]
            
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=ssh_data['hostname'],
            username=ssh_data['username'],
            password=ssh_data['password'],
            port=ssh_data.get('port', 22),
            timeout=5,
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
    def __init__(self, ttl: int = 30):
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
    def __init__(self, bot):
        """
        Инициализация системы мониторинга с адаптивным интервалом проверки.
        
        Args:
            bot: Объект бота для отправки уведомлений
        """
        self.bot = bot
        self.base_interval = 300  # базовый интервал 5 минут
        self.min_interval = 60    # минимальный интервал 1 минута
        self.monitoring_tasks = {}
        self.logger = logger
        self.ssh_pool = SSHPool()
        self.metrics_cache = MetricsCache()
        self.alert_states = {}
        self.last_alert_time = {}
        self.agent_port = 8080
        self.last_metrics = {}
        self.false_positive_threshold = 3
        self.high_load_counter = {}
        self.current_intervals = {}  # хранение текущих интервалов для каждого пользователя

        self.linux_commands = {
            'cpu': "cat /proc/loadavg | awk '{print $1*100/$(nproc)}'",
            'ram': "free | awk '/Mem:/ {print int($3/$2 * 100)}'",
            'disk': "df -P / | tail -1 | awk '{print int($5)}'"
        }

        self.windows_commands = {
            'cpu': 'powershell -command "$loadAvg=(Get-CimInstance Win32_Processor).LoadPercentage;Write-Output $loadAvg"',
            'ram': 'powershell -command "$os=Get-CimInstance Win32_OperatingSystem;Write-Output ([math]::Round(100-($os.FreePhysicalMemory/$os.TotalVisibleMemorySize*100)))"',
            'disk': 'powershell -command "$disk=Get-PSDrive C;Write-Output ([math]::Round(100-($disk.Free/($disk.Used+$disk.Free)*100)))"'
        }

        # Оптимизированные команды для Linux с использованием /proc и минимальной нагрузкой
        self.linux_commands = {
            'cpu': "cat /proc/loadavg | awk '{print $1*100/$(nproc)}'",  # Используем loadavg вместо текущей загрузки
            'ram': "free -b | awk '/Mem:/ {printf \"%.1f\", $3*100/$2}'",  # Используем байты для точности
            'disk': "df -P / | tail -1 | awk '{print int($5)}'"
        }
        
        # Оптимизированные команды для Windows
        self.windows_commands = {
            'cpu': 'powershell -command "$loadAvg=(Get-CimInstance Win32_Processor).LoadPercentage;Write-Output $loadAvg"',
            'ram': 'powershell -command "$os=Get-CimInstance Win32_OperatingSystem;$total=$os.TotalVisibleMemorySize;$free=$os.FreePhysicalMemory;Write-Output ([math]::Round(($total-$free)/$total*100,1))"',
            'disk': 'powershell -command "$disk=Get-PSDrive C;Write-Output ([math]::Round($disk.Used/($disk.Used+$disk.Free)*100,1))"'
        }

    def _calculate_check_interval(self, metrics):
        """
        Рассчитывает интервал проверки на основе текущих метрик.
        При высокой нагрузке уменьшает интервал, при низкой - увеличивает.
        """
        try:
            # Получаем значения метрик
            cpu = float(metrics.get('Загрузка процессора', 0))
            ram = float(metrics.get('Использование ОЗУ', 0))
            disk = float(metrics.get('Использование диска', 0))
            
            # Находим максимальную нагрузку среди всех ресурсов
            max_usage = max(cpu, ram, disk)
            
            if max_usage >= 90:  # Критическая нагрузка
                return self.min_interval
            elif max_usage >= 75:  # Высокая нагрузка
                return self.min_interval * 2
            elif max_usage >= 50:  # Средняя нагрузка
                return self.base_interval // 2
            else:  # Низкая нагрузка
                return self.base_interval
                
        except Exception as e:
            self.logger.error(f"Ошибка при расчете интервала: {e}")
            return self.base_interval

    async def start_monitoring(self, user_id, ssh_data):
        if user_id in self.monitoring_tasks:
            return False
        
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
        if user_id in self.monitoring_tasks:
            self.monitoring_tasks[user_id].cancel()
            del self.monitoring_tasks[user_id]
            if user_id in self.alert_states:
                del self.alert_states[user_id]
            self.logger.info(f"Остановлен мониторинг для пользователя {user_id}")
            return True
        return False

    def is_monitoring(self, user_id):
        return user_id in self.monitoring_tasks

    async def _monitor_loop(self, user_id, ssh_data):
        try:
            while True:
                try:
                    metrics = await self._get_metrics(user_id, ssh_data)
                    await self._check_thresholds(user_id, metrics)
                    
                    # Рассчитываем новый интервал на основе метрик
                    check_interval = self._calculate_check_interval(metrics)
                    self.current_intervals[user_id] = check_interval
                    
                except Exception as e:
                    self.logger.error(f"Ошибка в цикле мониторинга: {e}")
                    await self.bot.send_message(
                        user_id,
                        "❌ Ошибка при получении данных мониторинга. Мониторинг остановлен."
                    )
                    break

                await asyncio.sleep(check_interval)

        except asyncio.CancelledError:
            self.logger.info(f"Мониторинг отменен для пользователя {user_id}")
        finally:
            self.ssh_pool.close_connection(user_id)
            if user_id in self.monitoring_tasks:
                del self.monitoring_tasks[user_id]
            if user_id in self.current_intervals:
                del self.current_intervals[user_id]

    async def _get_metrics(self, user_id: int, ssh_data: dict) -> dict:
        cached_data = self.metrics_cache.get(user_id)
        if cached_data:
            return cached_data
            
        try:
            ssh = self.ssh_pool.get_connection(user_id, ssh_data)
            
            try:
                ssh.get_transport().request_port_forward('', self.agent_port, 'localhost', self.agent_port)
                
                _, stdout, _ = ssh.exec_command(f'curl -s http://localhost:{self.agent_port}/metrics', timeout=5)
                metrics_data = stdout.read().decode().strip()
                metrics = json.loads(metrics_data)
                
                self.metrics_cache.set(user_id, metrics)
                return metrics
                
            except Exception as agent_error:
                logger.warning(f"Не удалось получить метрики от агента: {agent_error}. Используем старый метод.")
                return await self._get_metrics_legacy(ssh)
            
        except Exception as e:
            logger.error(f"Ошибка при получении метрик: {e}")
            return {'cpu': 0, 'ram': 0, 'disk': 0}

    async def _get_metrics_legacy(self, ssh):
        """
        Получает метрики системы через SSH с минимальной нагрузкой.
        Использует легкие команды для сбора данных о CPU, RAM и диске.
        
        Args:
            ssh: Активное SSH-соединение
            
        Returns:
            dict: Словарь с метриками системы {cpu: float, ram: float, disk: float}
        """
        try:
            if not hasattr(self, '_cached_os_type'):
                self._cached_os_type = "windows" if self._is_windows(ssh) else "linux"
            
            commands = self.windows_commands if self._cached_os_type == "windows" else self.linux_commands
            
            # Выполняем все команды одновременно для оптимизации
            combined_cmd = " && ".join([f"echo '{k}='$({v})" for k, v in commands.items()])
            if self._cached_os_type == "windows":
                combined_cmd = 'powershell -command "' + combined_cmd + '"'
            
            _, stdout, _ = ssh.exec_command(combined_cmd, timeout=3)
            output = stdout.read().decode().strip()
            
            metrics = {}
            for line in output.split('\n'):
                if '=' in line:
                    key, value = line.strip().split('=')
                    try:
                        metrics[key] = float(value)
                    except ValueError:
                        metrics[key] = 0
            
            return metrics
            
        except Exception as e:
            logger.error(f"Ошибка при получении метрик: {e}")
            return None

    async def _check_thresholds(self, user_id: int, metrics: dict):
        """
        Проверяет метрики на превышение пороговых значений с учетом ложных срабатываний.
        Отправляет уведомления только при подтвержденных проблемах.
        
        Args:
            user_id: ID пользователя для отправки уведомлений
            metrics: Словарь с текущими метриками
        """
        if not metrics:
            return
            
        current_time = time.time()
        min_alert_interval = 3600
        
        if user_id not in self.alert_states:
            self.alert_states[user_id] = {k: False for k in THRESHOLDS.keys()}
            self.last_alert_time[user_id] = {k: 0 for k in THRESHOLDS.keys()}
            self.high_load_counter[user_id] = {k: 0 for k in THRESHOLDS.keys()}

        alerts_to_send = []
        resolved_alerts = []

        for resource in THRESHOLDS.keys():
            current_value = metrics.get(resource, 0)
            prev_value = self.last_metrics.get(user_id, {}).get(resource, 0)
            is_critical = current_value >= THRESHOLDS[resource]
            prev_state = self.alert_states[user_id][resource]
            last_alert = self.last_alert_time[user_id][resource]

            if is_critical and abs(current_value - prev_value) > 40:
                continue

            if is_critical:
                self.high_load_counter[user_id][resource] += 1
            else:
                self.high_load_counter[user_id][resource] = 0

            if (is_critical and 
                self.high_load_counter[user_id][resource] >= self.false_positive_threshold and 
                (not prev_state or current_time - last_alert >= min_alert_interval)):
                alerts_to_send.append((resource, current_value))
                self.last_alert_time[user_id][resource] = current_time
            elif not is_critical and prev_state:
                resolved_alerts.append(resource)
                self.high_load_counter[user_id][resource] = 0

            self.alert_states[user_id][resource] = is_critical

        self.last_metrics[user_id] = metrics

        if alerts_to_send:
            message = "⚠️ *Подтверждена высокая нагрузка:*\n\n"
            for resource, value in alerts_to_send:
                message += f"*{self._get_resource_name(resource)}:* {value:.1f}%\n"
                message += "*Рекомендации:*\n"
                for rec in RECOMMENDATIONS[resource]:
                    message += f"{rec}\n"
                message += "\n"
            
            await self.bot.send_message(user_id, message, parse_mode="Markdown")

        if resolved_alerts:
            message = "✅ *Нагрузка нормализовалась:*\n\n"
            for resource in resolved_alerts:
                message += f"*{self._get_resource_name(resource)}*\n"
            await self.bot.send_message(user_id, message, parse_mode="Markdown")

    async def _check_metrics(self, user_id, system_data):
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
        names = {
            'cpu': 'Загрузка процессора',
            'ram': 'Использование памяти',
            'disk': 'Использование диска'
        }
        return names.get(resource, resource)
