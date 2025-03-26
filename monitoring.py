import asyncio
from datetime import datetime, timedelta
import logging
import paramiko # type: ignore
from typing import Dict, Any, Optional, Tuple
import time
import json
from logger import logger

THRESHOLDS = {
    'cpu': 90.0,  # Типизируем как float
    'ram': 90.0,
    'disk': 90.0
}

LOG_MESSAGES = {
    'monitor_start': "Мониторинг запущен для {user_id}",
    'monitor_stop': "Мониторинг остановлен для {user_id}",
    'metrics_error': "Ошибка получения метрик: {error}",
    'high_load': "{resource}: {value:.1f}% (порог {threshold}%)",
    'load_normalized': "{resource} в норме: {value:.1f}%"
}

RECOMMENDATIONS = {
    'cpu': [
        "• Проверьте нагрузку процессов (top/htop)",
        "• Завершите неиспользуемые процессы",
        "• Рассмотрите масштабирование"
    ],
    'ram': [
        "• Очистите системный кэш",
        "• Проверьте утечки памяти",
        "• Увеличьте объем RAM/swap"
    ],
    'disk': [
        "• Очистите системные логи",
        "• Удалите временные файлы",
        "• Расширьте дисковое пространство"
    ]
}

class SSHPool:
    """Оптимизированный пул SSH-соединений."""
    def __init__(self, timeout: int = 300):
        self.connections: Dict[int, paramiko.SSHClient] = {}
        self.last_used: Dict[int, float] = {}
        self.timeout = timeout
        
    def get_connection(self, user_id: int, ssh_data: dict) -> Tuple[paramiko.SSHClient, bool]:
        """Получение существующего или создание нового соединения."""
        current_time = time.time()
        self._cleanup(current_time)
        
        is_new = False
        if user_id in self.connections:
            self.last_used[user_id] = current_time
            try:
                # Проверяем активность соединения
                self.connections[user_id].exec_command('echo 1', timeout=2)
                return self.connections[user_id], is_new
            except:
                self.close_connection(user_id)
        
        is_new = True
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
        return client, is_new

    def _cleanup(self, current_time: float):
        """Очистка неактивных соединений."""
        for user_id in list(self.connections.keys()):
            if current_time - self.last_used[user_id] > self.timeout:
                self.close_connection(user_id)
                
    def close_connection(self, user_id: int):
        """Безопасное закрытие соединения."""
        if user_id in self.connections:
            try:
                self.connections[user_id].close()
            except Exception as e:
                logger.error(f"Ошибка закрытия SSH соединения: {e}")
            finally:
                del self.connections[user_id]
                del self.last_used[user_id]

class MetricsCache:
    """Кэширование метрик с улучшенной валидацией."""
    def __init__(self, ttl: int = 30):
        self.cache: Dict[int, Dict[str, float]] = {}
        self.ttl = ttl
        self.last_update: Dict[int, float] = {}
        
    def get(self, user_id: int) -> Optional[Dict[str, float]]:
        if user_id in self.cache and time.time() - self.last_update[user_id] < self.ttl:
            return self.cache[user_id]
        return None
        
    def set(self, user_id: int, data: Dict[str, float]):
        self.cache[user_id] = data
        self.last_update[user_id] = time.time()
        
    def invalidate(self, user_id: int):
        """Инвалидация кэша для пользователя."""
        if user_id in self.cache:
            del self.cache[user_id]
            del self.last_update[user_id]

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
    """Оптимизированный монитор системы."""
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

    async def _get_metrics(self, user_id: int, ssh_data: dict) -> Dict[str, float]:
        """Получение метрик с оптимизированным кэшированием."""
        try:
            cached_data = self.metrics_cache.get(user_id)
            if cached_data:
                return cached_data

            client, is_new = self.ssh_pool.get_connection(user_id, ssh_data)
            
            try:
                # Определяем ОС только для новых соединений
                if is_new:
                    os_type = await self._detect_os_type(client)
                    ssh_data['os_type'] = os_type
                
                metrics = await self._collect_metrics(client, ssh_data.get('os_type', 'linux'))
                self.metrics_cache.set(user_id, metrics)
                return metrics
                
            except Exception as e:
                logger.error(f"Ошибка сбора метрик: {e}")
                self.metrics_cache.invalidate(user_id)
                return {}
                
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            return {}

    async def _detect_os_type(self, client: paramiko.SSHClient) -> str:
        """Определение типа ОС с кэшированием результата."""
        try:
            _, stdout, _ = client.exec_command('ver', timeout=5)
            return 'windows' if 'windows' in stdout.read().decode().lower() else 'linux'
        except:
            return 'linux'

    async def _collect_metrics(self, client: paramiko.SSHClient, os_type: str) -> Dict[str, float]:
        """Сбор метрик с валидацией значений."""
        commands = self.windows_commands if os_type == 'windows' else self.linux_commands
        metrics = {}

        for resource, command in commands.items():
            try:
                _, stdout, _ = client.exec_command(command, timeout=5)
                value = float(stdout.read().decode().strip())
                metrics[resource] = max(0.0, min(100.0, value))  # Нормализация значений
            except Exception as e:
                logger.error(f"Ошибка сбора метрики {resource}: {e}")
                metrics[resource] = 0.0

        return metrics

    async def _check_thresholds(self, user_id: int, metrics: Dict[str, float]):
        """Проверка пороговых значений с защитой от ложных срабатываний."""
        if not metrics:
            return

        current_time = time.time()
        alert_cooldown = 3600  # Кулдаун уведомлений - 1 час

        alerts = []
        resolved = []

        for resource, current_value in metrics.items():
            if resource not in THRESHOLDS:
                continue

            threshold = THRESHOLDS[resource]
            prev_state = self.alert_states.get(user_id, {}).get(resource, False)
            last_alert = self.last_alert_time.get(user_id, {}).get(resource, 0)

            if current_value >= threshold:
                if not prev_state or current_time - last_alert >= alert_cooldown:
                    alerts.append((resource, current_value, threshold))
                    self.last_alert_time.setdefault(user_id, {})[resource] = current_time
            elif prev_state:
                resolved.append((resource, current_value))

            self.alert_states.setdefault(user_id, {})[resource] = current_value >= threshold

        if alerts:
            message = "⚠️ *Критическая нагрузка:*\n\n"
            for resource, value, threshold in alerts:
                message += f"{LOG_MESSAGES['high_load'].format(resource=resource, value=value, threshold=threshold)}\n"
                message += "*Рекомендации:*\n" + "\n".join(RECOMMENDATIONS[resource]) + "\n\n"
            
            await self.bot.send_message(user_id, message, parse_mode="Markdown")

        if resolved:
            message = "✅ *Нагрузка нормализовалась:*\n"
            for resource, value in resolved:
                message += LOG_MESSAGES['load_normalized'].format(resource=resource, value=value) + "\n"
            
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
            'cpu': 'Процессор',
            'ram': 'Память',
            'disk': 'Диск'
        }
        return names.get(resource, resource)
