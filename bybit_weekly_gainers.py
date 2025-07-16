import sys
import asyncio
import aiohttp
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView, QLabel,
    QComboBox, QCheckBox, QDialog, QDialogButtonBox, QRadioButton, QHBoxLayout
)
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtGui import QColor, QBrush, QFont
import qasync

BYBIT_SYMBOLS_URL = "https://api.bybit.com/v5/market/instruments-info?category={category}"
BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline?category={category}&symbol={symbol}&interval=W&limit=3"
WS_URLS = {
    "spot": "wss://stream.bybit.com/v5/public/spot",
    "linear": "wss://stream.bybit.com/v5/public/linear"
}
CATEGORIES = ["spot", "linear"]

class ModeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Выбор режима")
        self.setFixedSize(300, 120)
        layout = QVBoxLayout(self)
        self.long_radio = QRadioButton("Лонг (рост)")
        self.short_radio = QRadioButton("Шорт (падение)")
        self.long_radio.setChecked(True)
        layout.addWidget(QLabel("Что искать?"))
        layout.addWidget(self.long_radio)
        layout.addWidget(self.short_radio)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        self.buttons.accepted.connect(self.accept)
        layout.addWidget(self.buttons)
    def is_long(self):
        return self.long_radio.isChecked()

class BybitWeeklyGainersWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bybit Weekly Gainers")
        self.setMinimumWidth(1100)
        self.setMinimumHeight(600)
        self.setStyleSheet(self.dark_stylesheet())
        layout = QVBoxLayout(self)
        self.status_label = QLabel("Загрузка...")
        self.status_label.setFont(QFont("Arial", 10))
        layout.addWidget(self.status_label)
        # --- Фильтр по типу ---
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Тип:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["Все", "spot", "linear"])
        self.type_combo.currentIndexChanged.connect(self.update_table)
        filter_layout.addWidget(self.type_combo)
        self.deep_check = QCheckBox("Глубокий анализ")
        self.deep_check.stateChanged.connect(self.update_table)
        filter_layout.addWidget(self.deep_check)
        filter_layout.addStretch(1)
        layout.addLayout(filter_layout)
        # --- Таблица ---
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "Тикер", "Тип", "Неделя 1", "Неделя 2", "Хай/Лоу недели 2", "Текущая цена(обн. 10 сек)"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        # Контекстное меню подключаем только один раз!
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        layout.addWidget(self.table)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_table)
        self.timer.start(2000)
        self.gainers = []
        self.prices = {}  # (symbol, category) -> price
        self.loop = None
        # --- Выбор режима ---
        dlg = ModeDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            self.is_long_mode = dlg.is_long()
        else:
            self.is_long_mode = True
        self.load_data()
        self.price_timer = QTimer(self)
        self.price_timer.timeout.connect(self.update_prices_async)
        self.price_timer.start(10000)  # 10 секунд

    def set_status(self, text):
        self.status_label.setText(text)

    def update_table(self):
        # --- Фильтрация по типу ---
        type_filter = self.type_combo.currentText()
        deep = self.deep_check.isChecked()
        filtered = []
        for row in self.gainers:
            symbol, category, close1, close2, high2, low2 = row
            if type_filter != "Все" and category != type_filter:
                continue
            price = self.prices.get((symbol, category), None)
            is_green = False
            if self.is_long_mode:
                if price is not None and price > high2:
                    is_green = True
                if deep and not is_green:
                    continue
            else:
                if price is not None and price < low2:
                    is_green = True
                if deep and not is_green:
                    continue
            filtered.append((symbol, category, close1, close2, high2, low2, price, is_green))
        self.table.setRowCount(len(filtered))
        for row, (symbol, category, close1, close2, high2, low2, price, is_green) in enumerate(filtered):
            self.table.setItem(row, 0, QTableWidgetItem(symbol))
            self.table.setItem(row, 1, QTableWidgetItem(category))
            self.table.setItem(row, 2, QTableWidgetItem(f"{close1:.8f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{close2:.8f}"))
            if self.is_long_mode:
                self.table.setItem(row, 4, QTableWidgetItem(f"{high2:.8f}"))
            else:
                self.table.setItem(row, 4, QTableWidgetItem(f"{low2:.8f}"))
            price_item = QTableWidgetItem(f"{price:.8f}" if price is not None else "-")
            if price is not None:
                if self.is_long_mode:
                    if price > high2:
                        price_item.setForeground(QBrush(QColor(0, 200, 0)))
                    elif price < high2:
                        price_item.setForeground(QBrush(QColor(220, 0, 0)))
                    else:
                        price_item.setForeground(QBrush(QColor(180, 180, 180)))
                else:
                    if price < low2:
                        price_item.setForeground(QBrush(QColor(0, 200, 0)))
                    elif price > low2:
                        price_item.setForeground(QBrush(QColor(220, 0, 0)))
                    else:
                        price_item.setForeground(QBrush(QColor(180, 180, 180)))
            self.table.setItem(row, 5, price_item)
        # Контекстное меню оставляем без изменений

    def show_context_menu(self, pos):
        from PyQt5.QtWidgets import QMenu, QAction
        index = self.table.indexAt(pos)
        if not index.isValid() or index.column() != 0:
            return
        row = index.row()
        symbol = self.table.item(row, 0).text()
        category = self.table.item(row, 1).text()
        menu = QMenu(self)
        copy_action = QAction("Скопировать тикер", self)
        tv_action = QAction("Открыть в TradingView", self)
        menu.addAction(copy_action)
        menu.addAction(tv_action)
        def copy_symbol():
            clipboard = QApplication.clipboard()
            clipboard.setText(symbol)
        def open_tv():
            import webbrowser
            tv_symbol = f"BYBIT:{symbol}"
            if category == 'linear':
                tv_symbol += '.P'
            url = f"https://www.tradingview.com/chart/?symbol={tv_symbol}"
            webbrowser.open(url)
        copy_action.triggered.connect(copy_symbol)
        tv_action.triggered.connect(open_tv)
        menu.exec_(self.table.viewport().mapToGlobal(pos))

    def closeEvent(self, event):
        for task in self.ws_tasks:
            task.cancel()
        event.accept()

    def load_data(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self.async_load())

    async def async_load(self):
        self.set_status("Получаем тикеры...")
        tickers = await self.get_all_tickers()
        self.set_status(f"Обрабатываем {len(tickers)} тикеров...")
        gainers = []
        for idx, (symbol, category) in enumerate(tickers):
            close1, close2, high2, low2 = await self.get_last_two_weekly_klines_ext(symbol, category)
            if close1 is not None and close2 is not None and high2 is not None and low2 is not None:
                if self.is_long_mode:
                    if close2 > close1:
                        gainers.append((symbol, category, close1, close2, high2, low2))
                else:
                    if close2 < close1:
                        gainers.append((symbol, category, close1, close2, high2, low2))
            if (idx+1) % 10 == 0:
                self.set_status(f"Проверено: {idx+1}/{len(tickers)}")
        self.set_status(f"Найдено: {len(gainers)} тикеров. Загружаем цены...")
        self.gainers = gainers
        await self.load_initial_prices()
        self.set_status(f"Готово. Найдено: {len(gainers)} тикеров.")

    async def load_initial_prices(self):
        # Получаем snapshot цен для spot и linear
        async with aiohttp.ClientSession() as session:
            for category in CATEGORIES:
                url = f"https://api.bybit.com/v5/market/tickers?category={category}"
                async with session.get(url) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    tickers_data = data.get('result', {}).get('list', [])
                    for ticker in tickers_data:
                        symbol = ticker.get('symbol')
                        price = ticker.get('lastPrice')
                        if price is not None:
                            try:
                                price = float(price)
                            except Exception:
                                continue
                            self.prices[(symbol, category)] = price
        self.update_table()  # Обновляем таблицу после загрузки цен

    async def get_all_tickers(self):
        tickers = []
        async with aiohttp.ClientSession() as session:
            for category in CATEGORIES:
                url = BYBIT_SYMBOLS_URL.format(category=category)
                async with session.get(url) as resp:
                    data = await resp.json()
                    for x in data['result']['list']:
                        tickers.append((x['symbol'], category))
        return tickers

    async def get_last_two_weekly_klines_ext(self, symbol, category):
        url = BYBIT_KLINE_URL.format(category=category, symbol=symbol)
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None, None, None, None
                data = await resp.json()
                klines = data.get('result', {}).get('list', [])
                if len(klines) < 3:
                    return None, None, None, None
                # Bybit API возвращает массив от новой к старой: [0] - текущая, [1] - последняя завершённая, [2] - предпоследняя завершённая
                prev = klines[2]  # Неделя 1 (предпоследняя завершённая)
                last = klines[1]  # Неделя 2 (последняя завершённая)
                close1 = float(prev[4])
                close2 = float(last[4])
                high2 = float(last[2])
                low2 = float(last[3])
                return close1, close2, high2, low2
        return None, None, None, None

    def update_prices_async(self):
        loop = asyncio.get_event_loop()
        loop.create_task(self.load_initial_prices())

    def dark_stylesheet(self):
        return """
        QWidget {
            background-color: #232629;
            color: #e0e0e0;
        }
        QHeaderView::section {
            background-color: #2c2f33;
            color: #e0e0e0;
            font-weight: bold;
            border: 1px solid #444;
        }
        QTableWidget {
            background-color: #232629;
            gridline-color: #444;
            selection-background-color: #44475a;
            selection-color: #f8f8f2;
        }
        QTableWidget QTableCornerButton::section {
            background-color: #2c2f33;
            border: 1px solid #444;
        }
        QLabel {
            color: #f8f8f2;
        }
        """

if __name__ == "__main__":
    app = QApplication(sys.argv)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    widget = BybitWeeklyGainersWidget()
    widget.show()
    with loop:
        loop.run_forever() 
