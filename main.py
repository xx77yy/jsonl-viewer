"""
Professional JSONL Dataset Viewer Application
A fully-featured, high-performance JSONL dataset viewer with streaming support,
search, filtering, schema detection, and table view capabilities.

Requirements:
pip install PyQt6 pygments
"""

import sys
import json
import gzip
import re
import threading
from pathlib import Path
from typing import Optional, Dict, List, Any, Set
from collections import defaultdict, Counter
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTreeWidget, QTreeWidgetItem, QPushButton, QLineEdit, QLabel,
    QFileDialog, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QComboBox, QSpinBox, QCheckBox, QProgressBar, QStatusBar,
    QGroupBox, QMessageBox, QToolBar, QMenuBar, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QAction, QFont, QColor, QPalette, QTextCharFormat, QSyntaxHighlighter


class JSONSyntaxHighlighter(QSyntaxHighlighter):
    """Syntax highlighter for JSON content"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.highlighting_rules = []
        
        # Keys (in quotes before colon)
        key_format = QTextCharFormat()
        key_format.setForeground(QColor("#569CD6"))
        key_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((r'"[^"]+"\s*:', key_format))
        
        # String values
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#CE9178"))
        self.highlighting_rules.append((r'"[^"]*"', string_format))
        
        # Numbers
        number_format = QTextCharFormat()
        number_format.setForeground(QColor("#B5CEA8"))
        self.highlighting_rules.append((r'\b\d+\.?\d*\b', number_format))
        
        # Booleans and null
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#569CD6"))
        self.highlighting_rules.append((r'\b(true|false|null)\b', keyword_format))
        
        # Brackets and braces
        bracket_format = QTextCharFormat()
        bracket_format.setForeground(QColor("#FFD700"))
        bracket_format.setFontWeight(QFont.Weight.Bold)
        self.highlighting_rules.append((r'[\[\]{}]', bracket_format))
    
    def highlightBlock(self, text):
        for pattern, fmt in self.highlighting_rules:
            for match in re.finditer(pattern, text):
                self.setFormat(match.start(), match.end() - match.start(), fmt)


class FileLoaderThread(QThread):
    """Thread for loading JSONL files incrementally"""
    
    progress = pyqtSignal(int, int)  # current, total
    line_loaded = pyqtSignal(int, dict, str)  # line_num, parsed_json, raw_line
    error_line = pyqtSignal(int, str, str)  # line_num, raw_line, error
    finished = pyqtSignal(dict)  # statistics
    
    def __init__(self, filepath: str, max_lines: Optional[int] = None):
        super().__init__()
        self.filepath = filepath
        self.max_lines = max_lines
        self.should_stop = False
        
    def run(self):
        stats = {
            'total_lines': 0,
            'valid_lines': 0,
            'error_lines': 0,
            'all_keys': set(),
            'key_counts': Counter(),
            'type_info': defaultdict(Counter)
        }
        
        try:
            # Determine if file is compressed
            opener = gzip.open if self.filepath.endswith('.gz') else open
            mode = 'rt' if self.filepath.endswith('.gz') else 'r'
            
            with opener(self.filepath, mode, encoding='utf-8', errors='replace') as f:
                for line_num, line in enumerate(f, 1):
                    if self.should_stop:
                        break
                    
                    if self.max_lines and line_num > self.max_lines:
                        break
                    
                    line = line.strip()
                    if not line:
                        continue
                    
                    stats['total_lines'] = line_num
                    
                    try:
                        parsed = json.loads(line)
                        stats['valid_lines'] += 1
                        
                        # Collect schema information
                        if isinstance(parsed, dict):
                            keys = set(parsed.keys())
                            stats['all_keys'].update(keys)
                            stats['key_counts'].update(keys)
                            
                            for key, value in parsed.items():
                                vtype = type(value).__name__
                                stats['type_info'][key][vtype] += 1
                        
                        self.line_loaded.emit(line_num, parsed, line)
                        
                    except json.JSONDecodeError as e:
                        stats['error_lines'] += 1
                        self.error_line.emit(line_num, line, str(e))
                    
                    if line_num % 100 == 0:
                        self.progress.emit(line_num, -1)
                        
        except Exception as e:
            self.error_line.emit(0, "", f"File error: {str(e)}")
        
        self.finished.emit(stats)
    
    def stop(self):
        self.should_stop = True


class JSONLViewer(QMainWindow):
    """Main JSONL Viewer Application"""
    
    def __init__(self):
        super().__init__()
        self.current_file = None
        self.all_records: List[Dict] = []
        self.error_records: List[tuple] = []
        self.filtered_indices: List[int] = []
        self.schema_info: Dict = {}
        self.loader_thread: Optional[FileLoaderThread] = None
        
        self.init_ui()
        self.apply_dark_theme()
        
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Professional JSONL Viewer")
        self.setGeometry(100, 100, 1400, 900)
        
        # Create menu bar
        self.create_menu_bar()
        
        # Create toolbar
        self.create_toolbar()
        
        # Main widget and layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)
        
        # File info panel
        file_info_layout = QHBoxLayout()
        self.file_label = QLabel("No file loaded")
        self.file_label.setStyleSheet("font-weight: bold; font-size: 12pt;")
        file_info_layout.addWidget(self.file_label)
        file_info_layout.addStretch()
        
        self.stats_label = QLabel("Lines: 0 | Valid: 0 | Errors: 0")
        file_info_layout.addWidget(self.stats_label)
        layout.addLayout(file_info_layout)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # Search and filter panel
        search_group = QGroupBox("Search & Filter")
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search text or regex...")
        self.search_input.textChanged.connect(self.on_search_changed)
        search_layout.addWidget(QLabel("Search:"))
        search_layout.addWidget(self.search_input)
        
        self.regex_checkbox = QCheckBox("Regex")
        search_layout.addWidget(self.regex_checkbox)
        
        self.field_filter_combo = QComboBox()
        self.field_filter_combo.addItem("All Fields")
        self.field_filter_combo.currentTextChanged.connect(self.apply_filters)
        search_layout.addWidget(QLabel("Field:"))
        search_layout.addWidget(self.field_filter_combo)
        
        self.clear_filter_btn = QPushButton("Clear Filters")
        self.clear_filter_btn.clicked.connect(self.clear_filters)
        search_layout.addWidget(self.clear_filter_btn)
        
        search_group.setLayout(search_layout)
        layout.addWidget(search_group)
        
        # Main content tabs
        self.tab_widget = QTabWidget()
        
        # Tree view tab
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabels(["Line #", "Content Preview"])
        self.tree_widget.setAlternatingRowColors(True)
        self.tree_widget.itemClicked.connect(self.on_tree_item_clicked)
        self.tab_widget.addTab(self.tree_widget, "Tree View")
        
        # Table view tab
        self.table_widget = QTableWidget()
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.setSortingEnabled(True)
        self.tab_widget.addTab(self.table_widget, "Table View")
        
        # Raw view tab
        self.raw_text = QTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setFont(QFont("Courier New", 10))
        self.highlighter = JSONSyntaxHighlighter(self.raw_text.document())
        self.tab_widget.addTab(self.raw_text, "Raw JSON")
        
        # Schema tab
        self.schema_tree = QTreeWidget()
        self.schema_tree.setHeaderLabels(["Field", "Type", "Count", "Coverage %"])
        self.schema_tree.setAlternatingRowColors(True)
        self.tab_widget.addTab(self.schema_tree, "Schema")
        
        # Errors tab
        self.errors_tree = QTreeWidget()
        self.errors_tree.setHeaderLabels(["Line #", "Error", "Content"])
        self.errors_tree.setAlternatingRowColors(True)
        self.tab_widget.addTab(self.errors_tree, "Errors")
        
        # Statistics tab
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setFont(QFont("Courier New", 10))
        self.tab_widget.addTab(self.stats_text, "Statistics")
        
        layout.addWidget(self.tab_widget)
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        
    def create_menu_bar(self):
        """Create application menu bar"""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("File")
        
        open_action = QAction("Open JSONL...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)
        
        file_menu.addSeparator()
        
        export_action = QAction("Export Filtered...", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_filtered)
        file_menu.addAction(export_action)
        
        export_csv_action = QAction("Export to CSV...", self)
        export_csv_action.triggered.connect(self.export_to_csv)
        file_menu.addAction(export_csv_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # View menu
        view_menu = menubar.addMenu("View")
        
        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut("F5")
        refresh_action.triggered.connect(self.refresh_views)
        view_menu.addAction(refresh_action)
        
        theme_action = QAction("Toggle Theme", self)
        theme_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(theme_action)
        
        # Help menu
        help_menu = menubar.addMenu("Help")
        
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
        
    def create_toolbar(self):
        """Create application toolbar"""
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        
        open_btn = QPushButton("📂 Open")
        open_btn.clicked.connect(self.open_file)
        toolbar.addWidget(open_btn)
        
        toolbar.addSeparator()
        
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_views)
        toolbar.addWidget(refresh_btn)
        
        toolbar.addSeparator()
        
        toolbar.addWidget(QLabel("Max Lines:"))
        self.max_lines_spin = QSpinBox()
        self.max_lines_spin.setRange(0, 10000000)
        self.max_lines_spin.setValue(10000)
        self.max_lines_spin.setSpecialValueText("All")
        toolbar.addWidget(self.max_lines_spin)
        
    def open_file(self):
        """Open a JSONL file"""
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "Open JSONL File",
            "",
            "JSONL Files (*.jsonl *.jsonl.gz);;All Files (*.*)"
        )
        
        if filepath:
            self.load_file(filepath)
    
    def load_file(self, filepath: str):
        """Load JSONL file in background thread"""
        self.current_file = filepath
        self.file_label.setText(f"Loading: {Path(filepath).name}")
        self.all_records.clear()
        self.error_records.clear()
        self.filtered_indices.clear()
        self.tree_widget.clear()
        self.errors_tree.clear()
        
        # Stop existing thread
        if self.loader_thread and self.loader_thread.isRunning():
            self.loader_thread.stop()
            self.loader_thread.wait()
        
        # Start new loader thread
        max_lines = self.max_lines_spin.value() if self.max_lines_spin.value() > 0 else None
        self.loader_thread = FileLoaderThread(filepath, max_lines)
        self.loader_thread.progress.connect(self.update_progress)
        self.loader_thread.line_loaded.connect(self.add_record)
        self.loader_thread.error_line.connect(self.add_error)
        self.loader_thread.finished.connect(self.on_loading_finished)
        
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # Indeterminate
        self.status_bar.showMessage("Loading file...")
        
        self.loader_thread.start()
    
    def update_progress(self, current: int, total: int):
        """Update progress bar"""
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
        self.status_bar.showMessage(f"Loaded {current:,} lines...")
    
    def add_record(self, line_num: int, parsed: dict, raw: str):
        """Add a successfully parsed record"""
        self.all_records.append({
            'line_num': line_num,
            'data': parsed,
            'raw': raw
        })
        
        # Add to tree view (throttled)
        if len(self.all_records) % 50 == 0 or len(self.all_records) < 100:
            self.add_tree_item(line_num, parsed)
    
    def add_tree_item(self, line_num: int, parsed: dict):
        """Add item to tree view"""
        preview = json.dumps(parsed, ensure_ascii=False)
        if len(preview) > 100:
            preview = preview[:100] + "..."
        
        item = QTreeWidgetItem([str(line_num), preview])
        item.setData(0, Qt.ItemDataRole.UserRole, line_num - 1)  # Store index
        self.tree_widget.addTopLevelItem(item)
    
    def add_error(self, line_num: int, raw: str, error: str):
        """Add a parsing error"""
        self.error_records.append((line_num, raw, error))
        
        item = QTreeWidgetItem([
            str(line_num) if line_num > 0 else "N/A",
            error,
            raw[:100] + "..." if len(raw) > 100 else raw
        ])
        self.errors_tree.addTopLevelItem(item)
    
    def on_loading_finished(self, stats: dict):
        """Called when file loading completes"""
        self.progress_bar.setVisible(False)
        self.schema_info = stats
        
        # Update UI
        self.file_label.setText(f"File: {Path(self.current_file).name}")
        self.stats_label.setText(
            f"Lines: {stats['total_lines']:,} | "
            f"Valid: {stats['valid_lines']:,} | "
            f"Errors: {stats['error_lines']:,}"
        )
        
        # Update field filter dropdown
        self.field_filter_combo.clear()
        self.field_filter_combo.addItem("All Fields")
        for key in sorted(stats['all_keys']):
            self.field_filter_combo.addItem(key)
        
        # Update schema view
        self.update_schema_view()
        
        # Update statistics
        self.update_statistics_view()
        
        # Initialize filtered indices
        self.filtered_indices = list(range(len(self.all_records)))
        
        self.status_bar.showMessage(
            f"Loaded {stats['valid_lines']:,} records successfully", 5000
        )
        
        # Populate table view
        self.populate_table_view()
    
    def update_schema_view(self):
        """Update the schema tree view"""
        self.schema_tree.clear()
        
        if not self.schema_info or 'key_counts' not in self.schema_info:
            return
        
        total_records = self.schema_info['valid_lines']
        
        for key in sorted(self.schema_info['all_keys']):
            count = self.schema_info['key_counts'][key]
            coverage = (count / total_records * 100) if total_records > 0 else 0
            
            types = self.schema_info['type_info'].get(key, {})
            type_str = ", ".join(f"{t}({c})" for t, c in types.most_common(3))
            
            item = QTreeWidgetItem([
                key,
                type_str,
                str(count),
                f"{coverage:.1f}%"
            ])
            self.schema_tree.addTopLevelItem(item)
        
        # Auto-resize columns
        for i in range(4):
            self.schema_tree.resizeColumnToContents(i)
    
    def update_statistics_view(self):
        """Update statistics text view"""
        if not self.schema_info:
            return
        
        stats_text = f"""
JSONL File Statistics
{'=' * 60}

File: {Path(self.current_file).name}
Loaded: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Records:
  Total Lines: {self.schema_info['total_lines']:,}
  Valid Records: {self.schema_info['valid_lines']:,}
  Parse Errors: {self.schema_info['error_lines']:,}
  Success Rate: {(self.schema_info['valid_lines'] / self.schema_info['total_lines'] * 100) if self.schema_info['total_lines'] > 0 else 0:.2f}%

Schema:
  Unique Fields: {len(self.schema_info['all_keys'])}
  
Field Coverage:
"""
        
        for key in sorted(self.schema_info['all_keys']):
            count = self.schema_info['key_counts'][key]
            coverage = (count / self.schema_info['valid_lines'] * 100) if self.schema_info['valid_lines'] > 0 else 0
            stats_text += f"  {key:30s}: {count:8,} ({coverage:5.1f}%)\n"
        
        self.stats_text.setText(stats_text)
    
    def on_tree_item_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle tree item click"""
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        if idx is not None and 0 <= idx < len(self.all_records):
            record = self.all_records[idx]
            formatted = json.dumps(record['data'], indent=2, ensure_ascii=False)
            self.raw_text.setText(formatted)
    
    def populate_table_view(self):
        """Populate table view with flattened data"""
        if not self.all_records:
            return
        
        # Get all unique keys
        all_keys = sorted(self.schema_info.get('all_keys', set()))
        
        # Use filtered indices
        display_records = [self.all_records[i] for i in self.filtered_indices[:1000]]  # Limit to 1000 rows
        
        self.table_widget.setRowCount(len(display_records))
        self.table_widget.setColumnCount(len(all_keys) + 1)
        self.table_widget.setHorizontalHeaderLabels(["Line #"] + all_keys)
        
        for row_idx, record in enumerate(display_records):
            # Line number
            self.table_widget.setItem(row_idx, 0, QTableWidgetItem(str(record['line_num'])))
            
            # Data fields
            data = record['data']
            for col_idx, key in enumerate(all_keys, 1):
                value = data.get(key, "")
                value_str = json.dumps(value, ensure_ascii=False) if not isinstance(value, (str, int, float, bool, type(None))) else str(value)
                self.table_widget.setItem(row_idx, col_idx, QTableWidgetItem(value_str))
        
        self.table_widget.resizeColumnsToContents()
    
    def on_search_changed(self):
        """Handle search input change"""
        QTimer.singleShot(300, self.apply_filters)  # Debounce
    
    def apply_filters(self):
        """Apply search and field filters"""
        if not self.all_records:
            return
        
        search_text = self.search_input.text().strip()
        use_regex = self.regex_checkbox.isChecked()
        field_filter = self.field_filter_combo.currentText()
        
        if not search_text and field_filter == "All Fields":
            self.filtered_indices = list(range(len(self.all_records)))
        else:
            self.filtered_indices = []
            
            if use_regex and search_text:
                try:
                    pattern = re.compile(search_text, re.IGNORECASE)
                except re.error:
                    self.status_bar.showMessage("Invalid regex pattern", 3000)
                    return
            
            for idx, record in enumerate(self.all_records):
                match = True
                
                # Field filter
                if field_filter != "All Fields":
                    if field_filter not in record['data']:
                        match = False
                
                # Search filter
                if match and search_text:
                    if field_filter != "All Fields":
                        search_in = str(record['data'].get(field_filter, ""))
                    else:
                        search_in = record['raw']
                    
                    if use_regex:
                        match = pattern.search(search_in) is not None
                    else:
                        match = search_text.lower() in search_in.lower()
                
                if match:
                    self.filtered_indices.append(idx)
        
        # Update tree view
        self.tree_widget.clear()
        for idx in self.filtered_indices[:1000]:  # Limit display
            record = self.all_records[idx]
            self.add_tree_item(record['line_num'], record['data'])
        
        # Update table view
        self.populate_table_view()
        
        self.status_bar.showMessage(
            f"Showing {len(self.filtered_indices):,} of {len(self.all_records):,} records",
            3000
        )
    
    def clear_filters(self):
        """Clear all filters"""
        self.search_input.clear()
        self.field_filter_combo.setCurrentIndex(0)
        self.regex_checkbox.setChecked(False)
        self.apply_filters()
    
    def refresh_views(self):
        """Refresh all views"""
        self.apply_filters()
        self.update_schema_view()
        self.update_statistics_view()
    
    def export_filtered(self):
        """Export filtered records to JSONL"""
        if not self.filtered_indices:
            QMessageBox.warning(self, "No Data", "No records to export")
            return
        
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export Filtered Records", "", "JSONL Files (*.jsonl)"
        )
        
        if filepath:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    for idx in self.filtered_indices:
                        record = self.all_records[idx]
                        f.write(json.dumps(record['data'], ensure_ascii=False) + '\n')
                
                QMessageBox.information(
                    self, "Success",
                    f"Exported {len(self.filtered_indices):,} records to {filepath}"
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Export failed: {str(e)}")
    
    def export_to_csv(self):
        """Export to CSV format"""
        if not self.all_records:
            QMessageBox.warning(self, "No Data", "No records to export")
            return
        
        filepath, _ = QFileDialog.getSaveFileName(
            self, "Export to CSV", "", "CSV Files (*.csv)"
        )
        
        if filepath:
            try:
                import csv
                all_keys = sorted(self.schema_info.get('all_keys', set()))
                
                with open(filepath, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(['line_num'] + all_keys)
                    
                    for idx in self.filtered_indices:
                        record = self.all_records[idx]
                        row = [record['line_num']]
                        for key in all_keys:
                            value = record['data'].get(key, '')
                            if isinstance(value, (dict, list)):
                                value = json.dumps(value, ensure_ascii=False)
                            row.append(str(value))
                        writer.writerow(row)
                
                QMessageBox.information(
                    self, "Success",
                    f"Exported {len(self.filtered_indices):,} records to CSV"
                )
            except Exception as e:
                QMessageBox.critical(self, "Error", f"CSV export failed: {str(e)}")
    
    def apply_dark_theme(self):
        """Apply dark theme to the application"""
        dark_stylesheet = """
            QMainWindow, QWidget {
                background-color: #1e1e1e;
                color: #d4d4d4;
            }
            QTreeWidget, QTableWidget, QTextEdit {
                background-color: #252526;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
            }
            QTreeWidget::item:selected, QTableWidget::item:selected {
                background-color: #094771;
            }
            QLineEdit, QSpinBox, QComboBox {
                background-color: #3c3c3c;
                color: #d4d4d4;
                border: 1px solid #555555;
                padding: 4px;
            }
            QPushButton {
                background-color: #0e639c;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 2px;
            }
            QPushButton:hover {
                background-color: #1177bb;
            }
            QGroupBox {
                border: 1px solid #3c3c3c;
                margin-top: 8px;
                padding-top: 8px;
            }
            QGroupBox::title {
                color: #d4d4d4;
            }
            QTabWidget::pane {
                border: 1px solid #3c3c3c;
            }
            QTabBar::tab {
                background-color: #2d2d30;
                color: #d4d4d4;
                padding: 8px 16px;
                border: 1px solid #3c3c3c;
            }
            QTabBar::tab:selected {
                background-color: #1e1e1e;
            }
            QStatusBar {
                background-color: #007acc;
                color: white;
            }
            QMenuBar {
                background-color: #2d2d30;
                color: #d4d4d4;
            }
            QMenuBar::item:selected {
                background-color: #094771;
            }
            QMenu {
                background-color: #252526;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
            }
            QMenu::item:selected {
                background-color: #094771;
            }
        """
        self.setStyleSheet(dark_stylesheet)
    
    def toggle_theme(self):
        """Toggle between dark and light theme"""
        if hasattr(self, '_dark_theme') and self._dark_theme:
            self.setStyleSheet("")  # Reset to default light theme
            self._dark_theme = False
        else:
            self.apply_dark_theme()
            self._dark_theme = True
    
    def show_about(self):
        """Show about dialog"""
        about_text = """
<h2>Professional JSONL Viewer</h2>
<p>Version 1.0.0</p>
<p>A high-performance JSONL file viewer with advanced features:</p>
<ul>
<li>Incremental loading for large files</li>
<li>Syntax highlighting</li>
<li>Search and filtering</li>
<li>Schema detection</li>
<li>Multiple view modes</li>
<li>Export capabilities</li>
<li>Compressed file support (.gz)</li>
</ul>
<p><b>Keyboard Shortcuts:</b></p>
<ul>
<li>Ctrl+O: Open file</li>
<li>Ctrl+E: Export filtered</li>
<li>Ctrl+Q: Quit</li>
<li>F5: Refresh views</li>
</ul>
        """
        QMessageBox.about(self, "About JSONL Viewer", about_text)
    
    def closeEvent(self, event):
        """Handle application close"""
        if self.loader_thread and self.loader_thread.isRunning():
            self.loader_thread.stop()
            self.loader_thread.wait()
        event.accept()


def main():
    """Main application entry point"""
    app = QApplication(sys.argv)
    app.setApplicationName("JSONL Viewer")
    app.setOrganizationName("Professional Tools")
    
    viewer = JSONLViewer()
    viewer.show()
    
    # Handle command line argument
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        if Path(filepath).exists():
            viewer.load_file(filepath)
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()