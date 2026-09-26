"""AI Shorts Maker Pro native desktop entrypoint."""
import sys
from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet('''
        QWidget { font-family: Segoe UI, Arial; font-size: 12px; background: #121924; color: #e6edf5; }
        QLabel#heading { font-size: 25px; font-weight: 700; color: #e9f3ff; padding: 12px 0; }
        QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit,
        QListWidget, QTableWidget { background: #1d2938; border: 1px solid #34475e; padding: 5px; }
        QPushButton { background: #2268b0; border: 0; border-radius: 4px; padding: 8px 14px; font-weight: 600; }
        QPushButton:hover { background: #3082d3; }
        QListWidget::item:selected, QTableWidget::item:selected { background: #225991; }
    ''')
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
