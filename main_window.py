"""Hlavní okno aplikace: šachovnice, přehrávání PGN a analýza enginem."""
from __future__ import annotations

import io
import json
import os
from datetime import date as _date

import chess
import chess.svg
from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSeries,
    QBarSet,
    QCategoryAxis,
    QChart,
    QChartView,
    QHorizontalBarSeries,
    QLineSeries,
    QPieSeries,
    QPolarChart,
    QScatterSeries,
    QValueAxis,
)
from PySide6.QtCore import QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGraphicsScene,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from accuracy import (
    TACT_CRIT_THRESHOLD,
    composite_index,
    quick_summary,
    win_prob,
    win_series,
)
from accuracy_batch import AccuracyBatch
from analysis_cache import AnalysisCache
from board_widget import BoardWidget
from tactics import STATUS_LABEL, TacticsStore, extract_puzzles
from motifs import MOTIF_LABEL, MOTIF_ORDER, motif_labels
from charakter import volatility as _volatility
from endgames import PIECE_LEGEND, analyze_endgames, pawns_cz, sort_key
from engine_analyzer import EngineWorker
from guess_move import GuessMoveDialog
from engine_perf import DEFAULT_HASH_MB, DEFAULT_PARALLEL, clamp_parallel, default_threads
from eval_bar import EvalBar
from filters import ELO_CHOICES, TIME_CLASSES, game_year, make_filter, years_range
from game_analyzer import MARK_BG, MARK_FG, MARK_KINDS, MARK_TEXT, GameAnalyzer
from move_class import (
    CC_MARK_BG,
    CC_MARK_FG,
    CC_MARK_KINDS,
    CC_MARK_TEXT,
    KIND_COLOR,
    KIND_LABEL,
    KIND_ORDER,
)
from heatmap_widget import HeatmapWidget
from histogram_widget import HistogramChart
from time_heatmap_widget import TimeHeatmap
from openings import analyze_openings, personal_book_depth, repertoire_diversity
from patterns import (
    analyze_patterns,
    distribution_stats,
    game_log,
    rating_pairs,
    to_prague_local,
)
from stats_util import (
    elo_expected_from_delta,
    estimate_shrink_m,
    kaplan_meier,
    shrink_rate,
    wilson_interval,
    winrate_outliers,
)
import report_charts
from anomaly import detect_anomalies
from pgn_game import LoadedGame, read_games
from player_report import (
    COMPARE_ROWS,
    ReportStore,
    best_index,
    build_snapshot,
    extract_metrics,
)
from player_analysis import (
    build_position_tree,
    collect_players,
    game_matches,
    heatmap_counts,
)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


class PgnLoader(QThread):
    """Načte PGN na pozadí, ať se GUI nezasekne u velkých databází."""

    progress = Signal(int)
    loaded = Signal(list)
    failed = Signal(str)

    def __init__(self, *, path: str | None = None, text: str | None = None) -> None:
        super().__init__()
        self._path = path
        self._text = text

    def run(self) -> None:
        try:
            fh = (open(self._path, "r", encoding="utf-8-sig", errors="replace")
                  if self._path is not None else io.StringIO(self._text))
            games = []
            try:
                for game in read_games(fh):
                    games.append(game)
                    if len(games) % 250 == 0:
                        self.progress.emit(len(games))
            finally:
                if self._path is not None:
                    fh.close()
            # klíče pozic pro strom zahájení – radši tady na pozadí než při 1. tahu
            for i, game in enumerate(games):
                game._ensure_keys()
                if i % 500 == 0:
                    self.progress.emit(len(games))
            self.loaded.emit(games)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))


class ReportBuilder(QThread):
    """Sestaví snímek statistik hráče (Vzorce + rozdělení + zahájení + deník) na
    pozadí – tyhle průchody přes celou databázi trvají u velkých PGN i minuty."""

    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, games, player: str, keep, acc_res, filter_desc: str,
                 parent=None) -> None:
        super().__init__(parent)
        self._games = games
        self._player = player
        self._keep = keep
        self._acc = acc_res
        self._filter_desc = filter_desc

    def run(self) -> None:
        try:
            g, pl, keep = self._games, self._player, self._keep
            pat = analyze_patterns(g, pl, "both", keep=keep)
            op_rows = []
            for grp in analyze_openings(g, pl, "both", keep=keep):
                rs = grp.results()
                op_rows.append({"label": grp.label, "w": rs.count("win"),
                                "d": rs.count("draw"), "l": rs.count("loss"),
                                "n": len(rs)})
            dist = distribution_stats(g, pl, "both", keep=keep)
            dist_ser = {
                "length": [e.moves for e in dist],
                "first_capture": [e.first_capture_move for e in dist],
                "endgame_entry": [e.eg_entry_move for e in dist],
                "deficit": [e.my_max_deficit for e in dist],
                "elo_delta": [e.elo_delta for e in dist],
                "result": [e.result for e in dist],
            }
            glog = [[dt.isoformat() if dt else None, ed, res]
                    for dt, ed, res in game_log(g, pl, "both", keep=keep)]
            rpairs = [[m, o, r] for m, o, r in rating_pairs(g, pl, "both", keep=keep)]
            snap = build_snapshot(pl, self._acc, pat, self._filter_desc,
                                  openings=op_rows, distribution=dist_ser,
                                  game_log=glog, rating_pairs=rpairs)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
            return
        self.done.emit(snap)


class TacticsExtractor(QThread):
    """Vytáhne taktické úlohy z rozboru na pozadí – u tisíců analyzovaných
    partií (detekce motivů, obtížnosti, oběti u každé úlohy) to je práce na
    desítky sekund a nesmí zaseknout GUI."""

    done = Signal(list)
    failed = Signal(str)

    def __init__(self, games, player, keep, cache, depth, parent=None,
                 use_wdl: bool = True) -> None:
        super().__init__(parent)
        self._games = games
        self._player = player
        self._keep = keep
        self._cache = cache
        self._depth = depth
        self._use_wdl = bool(use_wdl)

    def run(self) -> None:
        try:
            pz = extract_puzzles(self._games, self._player, "both",
                                 self._keep, self._cache, self._depth,
                                 use_wdl=self._use_wdl)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc))
            return
        self.done.emit(pz)


PLY_ROLE = Qt.UserRole + 1
LINE_ROLE = Qt.UserRole + 2
PATH_ROLE = Qt.UserRole + 3
WDL_ROLE = Qt.UserRole + 4
EG_GAME_ROLE = Qt.UserRole + 5
OP_GAME_ROLE = Qt.UserRole + 6
SORT_ROLE = Qt.UserRole + 7
ACC_GAME_ROLE = Qt.UserRole + 8
ACC_PLY_ROLE = Qt.UserRole + 9


class SortableItem(QTreeWidgetItem):
    """Řadí se podle hodnoty uložené v SORT_ROLE daného sloupce (jinak podle textu)."""

    def __lt__(self, other):
        tw = self.treeWidget()
        col = tw.sortColumn() if tw is not None else 0
        a = self.data(col, SORT_ROLE)
        b = other.data(col, SORT_ROLE)
        if a is None:
            a = self.text(col)
        if b is None:
            b = other.text(col)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)

_PIECE_CHOICES = [
    ("vše", None), ("pěšec", chess.PAWN), ("jezdec", chess.KNIGHT),
    ("střelec", chess.BISHOP), ("věž", chess.ROOK), ("dáma", chess.QUEEN),
    ("král", chess.KING),
]
_COLOR_CHOICES = [("jako bílý", "white"), ("jako černý", "black"), ("obě barvy", "both")]

_WIN_COLOR = QColor("#5aa457")
_DRAW_COLOR = QColor("#c9c9c9")
_LOSS_COLOR = QColor("#c9524f")




def _winrate(results) -> float | None:
    w = results.count("win")
    d = results.count("draw")
    lo = results.count("loss")
    n = w + d + lo
    return w / n if n else None


def _stat_item(label: str, results: list, name_key=None,
               p0: float | None = None, shrink_m: float | None = None,
               sig: tuple | None = None) -> SortableItem:
    """Řádek: název | počet partií | winrate % | pruh V/R/P; s klíči pro řazení.

    ``p0``/``shrink_m`` (viz :func:`_report_prior`) → tooltip u winrate ukáže
    Wilsonův interval a stažený (empirical-Bayes) odhad. ``sig`` = ``(p, True)``
    z :func:`stats_util.winrate_outliers` → winrate se zvýrazní (▲/▼, tučně),
    pokud se koš po Benjamini–Hochberg korekci významně liší od tvého celku.
    """
    w = results.count("win")
    d = results.count("draw")
    lo = results.count("loss")
    n = len(results)
    decided = w + d + lo
    wr = _winrate(results)
    pval, is_sig = (sig if sig is not None else (None, False))
    wr_txt = "–"
    if wr is not None:
        arrow = ""
        if is_sig and p0 is not None:
            arrow = " ▲" if wr > p0 else " ▼"
        wr_txt = f"{wr * 100:.0f} %{arrow}"
    it = SortableItem([label, str(n), wr_txt, ""])
    it.setData(3, WDL_ROLE, (w, d, lo))
    it.setBackground(2, _score_brush(wr))
    it.setData(0, SORT_ROLE, name_key if name_key is not None else label.casefold())
    it.setData(1, SORT_ROLE, n)
    it.setData(2, SORT_ROLE, wr if wr is not None else -1.0)
    it.setData(3, SORT_ROLE, wr if wr is not None else -1.0)
    for c in (1, 2):
        it.setTextAlignment(c, Qt.AlignCenter)
    if is_sig:
        f2 = it.font(2)
        f2.setBold(True)
        it.setFont(2, f2)
    if wr is not None and decided > 0:
        ci = wilson_interval(w, decided)
        tip = (f"Wilsonův 95% interval spolehlivosti: {ci[0] * 100:.0f}–{ci[1] * 100:.0f} %"
              if ci else "")
        if p0 is not None and shrink_m is not None:
            sr = shrink_rate(w, decided, p0, shrink_m)
            tip += (f"\nStažený odhad (k celkovému průměru {p0 * 100:.0f} %, "
                    f"síla {shrink_m:.0f} partií): {sr * 100:.0f} %")
        if pval is not None:
            tip += (f"\np vs. tvůj celkový winrate: {pval:.3f}"
                    + ("  → po BH korekci významné (FDR 5 %)" if is_sig
                       else "  (po korekci nevýznamné)"))
        if tip:
            it.setToolTip(2, tip.strip())
    return it


def _report_prior(all_results: list, bucket_results: list[list]) -> tuple[float, float]:
    """Celkový winrate (p0) a odhadnutá síla shrinkage (m) pro jeden rozbor –
    spočti jednou za report a předej všem řádkům přes ``_stat_item``."""
    w = all_results.count("win")
    dec = w + all_results.count("draw") + all_results.count("loss")
    p0 = w / dec if dec else 0.5
    buckets = []
    for res in bucket_results:
        bw = res.count("win")
        bdec = bw + res.count("draw") + res.count("loss")
        if bdec > 0:
            buckets.append((bw, bdec))
    m = estimate_shrink_m(buckets) if len(buckets) >= 2 else 10.0
    return p0, m


def _overall_wr(all_results: list) -> tuple[int, int]:
    w = all_results.count("win")
    return w, w + all_results.count("draw") + all_results.count("loss")


def _sig_flags(bucket_results: list, w0: int, n0: int) -> list:
    """Pro každou sadu výsledků vrátí (p, is_sig) proti celkovému winrate (w0/n0),
    po Benjamini–Hochberg korekci napříč koši."""
    buckets = [(res.count("win"),
                res.count("win") + res.count("draw") + res.count("loss"))
               for res in bucket_results]
    flags, pvals = winrate_outliers(buckets, w0, n0)
    return list(zip(pvals, flags))


def _bucket_shrink_m(rows) -> float:
    """Síla shrinkage z jedné konkrétní sady košů (ne z celého, různorodého reportu –
    koše stejného rozkladu mají srovnatelný rozptyl, napříč rozklady ne)."""
    buckets = []
    for _, res in rows:
        w = res.count("win")
        dec = w + res.count("draw") + res.count("loss")
        if dec > 0:
            buckets.append((w, dec))
    return estimate_shrink_m(buckets) if len(buckets) >= 2 else 10.0


def _repertoire_text(div: dict, book) -> str:
    """Diverzita repertoáru + hloubka teorie (jmenovaná vs. vlastní historie)."""
    parts = []
    eco, var = div.get("by_eco") or {}, div.get("by_variant") or {}
    if eco.get("n"):
        parts.append(
            f"Diverzita repertoáru: efektivně {eco['n_eff']:.1f} ECO rodin (z {eco['n']}), "
            f"{var['n_eff']:.1f} konkrétních variant (z {var['n']}); top 3 zahájení "
            f"pokrývají {eco['top3_share'] * 100:.0f} % partií.")
    if book.n:
        em, nm, gp = book.exit_moves(), book.named_moves(), book.gaps()
        mean_e, mean_n, mean_g = sum(em) / len(em), sum(nm) / len(nm), sum(gp) / len(gp)
        rel = f"{'+' if mean_g >= 0 else ''}{mean_g:.1f} tahu {'za' if mean_g >= 0 else 'před'}"
        parts.append(
            f"Hloubka teorie: podle jmenovaných zahájení v průměru do {mean_n:.1f}. tahu, "
            f"podle vlastní historie (bez externí databáze) do {mean_e:.1f}. tahu "
            f"({rel} jmenovanou knihou).")
    return "  ".join(parts)


def _score_brush(score: float | None) -> QBrush:
    if score is None:
        return QBrush(QColor("#efefef"))
    if score < 0.5:
        r, g = 224, round(70 + 300 * score)
    else:
        r, g = round(224 - 340 * (score - 0.5)), 200
    return QBrush(QColor(max(0, min(255, r)), max(0, min(255, g)), 66, 95))


class WDLBarDelegate(QStyledItemDelegate):
    """Vykreslí podíl výhra/remíza/prohra jako skládaný pruh."""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(160, 22)

    def paint(self, painter, option, index):  # noqa: N802
        wdl = index.data(WDL_ROLE)
        total = sum(wdl) if wdl else 0
        if not wdl or total == 0:
            super().paint(painter, option, index)
            return
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        w, d, lo = wdl
        r = option.rect.adjusted(5, 4, -5, -4)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        x = float(r.x())
        for val, col in ((w, _WIN_COLOR), (d, _DRAW_COLOR), (lo, _LOSS_COLOR)):
            if val <= 0:
                continue
            seg = r.width() * val / total
            painter.setPen(Qt.NoPen)
            painter.setBrush(col)
            painter.drawRect(QRectF(x, r.y(), seg, r.height()))
            if seg >= 24:
                lum = 0.299 * col.red() + 0.587 * col.green() + 0.114 * col.blue()
                painter.setPen(QColor("#ffffff") if lum < 150 else QColor("#333333"))
                painter.drawText(QRectF(x, r.y(), seg, r.height()),
                                 Qt.AlignCenter, f"{round(val * 100 / total)}%")
            x += seg
        painter.setPen(QColor("#00000030"))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(r))
        painter.restore()


def _board_key(fen: str) -> str:
    """Piece placement + strana na tahu – pro porovnání dvou pozic bez ohledu na počítadla."""
    parts = fen.split()
    return " ".join(parts[:2]) if len(parts) >= 2 else fen


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
    except Exception:
        pass


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Šachy – přehrávání PGN a analýza")
        self.resize(1300, 840)

        self.config = load_config()
        self.games: list[LoadedGame] = []
        self.game: LoadedGame | None = None
        self.ply = 0
        # vlastní tahy zahrané uživatelem od pozice self.ply (režim analýzy)
        self.analysis_moves: list[chess.Move] = []

        self.engine: EngineWorker | None = None
        self._engine_name = ""
        self._player_counts = collect_players([])

        # rozbory na kartách Heatmapa / Zahájení / Koncovky se spouští ručně
        self._analysis_ready: set[str] = set()
        # líné rozbalování stromu zahájení (Rozbor pozice)
        self._op_pending: dict = {}
        # filtr databáze (rok / tempo / soupeř)
        self._filter_keep = None
        # rozbor aktuální partie enginem
        self._game_eval: dict | None = None
        self._crit_plies: list = []
        self._game_analyzer: GameAnalyzer | None = None
        # dávkový rozbor přesnosti (karta Přesnost) + sdílená cache rozborů
        self._acc_batch: AccuracyBatch | None = None
        self._acc_cache: AnalysisCache | None = None
        self._acc_last_result: dict | None = None   # pro kartu Grafy (trend, volatilita)
        self._dist_cache: tuple | None = None        # pro kartu Grafy (histogramy z celé DB)
        # karta Taktika
        self._tactics_store = TacticsStore()
        self._tactics_all: list = []                 # všechny vytažené úlohy
        self._tactics_shown: list = []               # po filtru, v pořadí tabulky
        self._tactics_cur = None                     # aktuálně řešená úloha
        self._tactics_attempted = False              # pokus o aktuální úlohu už započítán?
        self._tactics_wrong = 0                      # špatných pokusů o aktuální úlohu
        self._tactics_extractor: TacticsExtractor | None = None
        # karta Report (uložené snímky statistik hráčů + porovnání)
        self._report_store = ReportStore()
        self._report_compare: list = []              # aktuálně porovnávané snímky
        self._report_builder: ReportBuilder | None = None

        # přehrávání varianty na malé šachovnici
        self._var_base: chess.Board | None = None
        self._var_moves: list[chess.Move] = []
        self._var_idx = 0
        self._var_line_idx: int | None = None
        self._var_pv_cache: list[str] = []

        self.autoplay = QTimer(self)
        self.autoplay.setInterval(1200)
        self.autoplay.timeout.connect(self._autoplay_step)

        self._build_ui()
        self._build_menu()

        self._load_startposition()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)

        # horní lišta se sdíleným výběrem hráče
        tbar = QToolBar("Hráč")
        tbar.setMovable(False)
        tbar.setFloatable(False)
        self.addToolBar(tbar)
        tbar.addWidget(QLabel("  Hráč pro rozbor:  "))
        self.cmb_player = QComboBox()
        self.cmb_player.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb_player.setMinimumContentsLength(16)
        self.cmb_player.currentIndexChanged.connect(self._on_player_changed)
        tbar.addWidget(self.cmb_player)

        outer.addWidget(self._build_filter_row())

        self.top_tabs = QTabWidget()
        outer.addWidget(self.top_tabs)
        self._board_tab_index = 0

        # =================== karta 1: šachovnice + rozbor ===================
        board_page = QWidget()
        root = QHBoxLayout(board_page)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter)

        # --- levý sloupec: šachovnice + navigace ---
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)

        self.board = BoardWidget()
        self.board.user_move.connect(self._on_user_move)
        self.eval_bar = EvalBar()
        board_row = QHBoxLayout()
        board_row.setContentsMargins(0, 0, 0, 0)
        board_row.addWidget(self.eval_bar)
        board_row.addWidget(self.board, stretch=1)
        left_l.addLayout(board_row, stretch=1)

        nav = QHBoxLayout()
        self.btn_start = QPushButton("⏮")
        self.btn_prev = QPushButton("◀")
        self.btn_play = QPushButton("▶ Přehrát")
        self.btn_next = QPushButton("▶")
        self.btn_end = QPushButton("⏭")
        self.btn_flip = QPushButton("⟳ Otočit")
        for b in (self.btn_start, self.btn_prev, self.btn_play, self.btn_next, self.btn_end, self.btn_flip):
            nav.addWidget(b)
        left_l.addLayout(nav)

        self.btn_start.clicked.connect(lambda: self.set_ply(0))
        self.btn_prev.clicked.connect(self._go_prev)
        self.btn_next.clicked.connect(self._go_next)
        self.btn_end.clicked.connect(lambda: self.set_ply(self.game.ply_count if self.game else 0))
        self.btn_play.clicked.connect(self._toggle_autoplay)
        self.btn_flip.clicked.connect(self._flip_boards)

        self.analysis_label = QLabel("")
        self.analysis_label.setWordWrap(True)
        self.analysis_label.setStyleSheet("color: #1565c0;")
        self.btn_exit_analysis = QPushButton("↩ Zpět na partii")
        self.btn_exit_analysis.clicked.connect(self._exit_analysis)
        self.btn_exit_analysis.setVisible(False)
        ana_row = QHBoxLayout()
        ana_row.addWidget(self.analysis_label, stretch=1)
        ana_row.addWidget(self.btn_exit_analysis)
        left_l.addLayout(ana_row)

        speed = QHBoxLayout()
        speed.addWidget(QLabel("Rychlost přehrávání (ms):"))
        self.spin_speed = QSpinBox()
        self.spin_speed.setRange(200, 5000)
        self.spin_speed.setSingleStep(100)
        self.spin_speed.setValue(1200)
        self.spin_speed.valueChanged.connect(self.autoplay.setInterval)
        speed.addWidget(self.spin_speed)
        speed.addStretch(1)
        left_l.addLayout(speed)

        splitter.addWidget(left)

        # ---------------------- pravý panel (pod-záložky) ----------------------
        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(4, 4, 4, 4)

        self.tabs = QTabWidget()
        right_l.addWidget(self.tabs, stretch=1)

        # ---------------- záložka Partie ----------------
        tab_game = QWidget()
        tg = QVBoxLayout(tab_game)
        self.game_selector = QComboBox()
        self.game_selector.currentIndexChanged.connect(self._on_game_selected)
        tg.addWidget(self.game_selector)
        self.header_label = QLabel("Žádná partie – otevři PGN (Ctrl+O)")
        self.header_label.setWordWrap(True)
        self.header_label.setStyleSheet("font-weight: bold;")
        tg.addWidget(self.header_label)
        self.move_table = QTableWidget(0, 3)
        self.move_table.setHorizontalHeaderLabels(["#", "Bílý", "Černý"])
        self.move_table.verticalHeader().setVisible(False)
        self.move_table.setColumnWidth(0, 40)
        self.move_table.horizontalHeader().setStretchLastSection(True)
        self.move_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.move_table.setSelectionMode(QTableWidget.NoSelection)
        self.move_table.cellClicked.connect(self._on_move_cell_clicked)
        tg.addWidget(self.move_table, stretch=1)
        self.comment_label = QLabel("")
        self.comment_label.setWordWrap(True)
        self.comment_label.setStyleSheet("color: #555; font-style: italic;")
        tg.addWidget(self.comment_label)

        ga_row = QHBoxLayout()
        self.btn_game_analyze = QPushButton("⚙ Rozebrat partii enginem")
        self.btn_game_analyze.clicked.connect(self._toggle_game_analysis)
        ga_row.addWidget(self.btn_game_analyze)
        ga_row.addWidget(QLabel("hloubka:"))
        self.spin_ga_depth = QSpinBox()
        self.spin_ga_depth.setRange(6, 22)
        self.spin_ga_depth.setValue(int(self.config.get("ga_depth", 12)))
        ga_row.addWidget(self.spin_ga_depth)
        ga_row.addWidget(QLabel("styl:"))
        self.cmb_ga_style = QComboBox()
        self.cmb_ga_style.addItem("Lichess (??/?/?!)", "lichess")
        self.cmb_ga_style.addItem("Chess.com (!!/!/??/?/?!/✗)", "chesscom")
        style_idx = self.cmb_ga_style.findData(self.config.get("ga_style", "lichess"))
        self.cmb_ga_style.setCurrentIndex(max(0, style_idx))
        self.cmb_ga_style.currentIndexChanged.connect(self._on_ga_style_changed)
        ga_row.addWidget(self.cmb_ga_style)
        ga_row.addStretch(1)
        self.btn_guess_move = QPushButton("🎓 Hádej tah")
        self.btn_guess_move.setToolTip("Přehraj partii se skrytým enginem a u každého "
                                       "svého tahu tipni – ohodnotí se přesnost.")
        self.btn_guess_move.clicked.connect(self._open_guess_move)
        ga_row.addWidget(self.btn_guess_move)
        tg.addLayout(ga_row)
        self.ga_summary = QLabel("")
        self.ga_summary.setWordWrap(True)
        self.ga_summary.setStyleSheet("color:#555;")
        tg.addWidget(self.ga_summary)

        self.crit_row = QWidget()
        cr = QHBoxLayout(self.crit_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addWidget(QLabel("Kritické momenty:"))
        self.btn_crit_prev = QPushButton("◀")
        self.btn_crit_prev.setFixedWidth(30)
        self.btn_crit_prev.clicked.connect(lambda: self._step_crit(-1))
        cr.addWidget(self.btn_crit_prev)
        self.cmb_crit = QComboBox()
        self.cmb_crit.setMinimumWidth(260)
        self.cmb_crit.activated.connect(self._on_crit_selected)
        cr.addWidget(self.cmb_crit)
        self.btn_crit_next = QPushButton("▶")
        self.btn_crit_next.setFixedWidth(30)
        self.btn_crit_next.clicked.connect(lambda: self._step_crit(1))
        cr.addWidget(self.btn_crit_next)
        cr.addStretch(1)
        self.crit_row.hide()
        tg.addWidget(self.crit_row)

        self.ga_legend = QLabel("")
        self.ga_legend.setTextFormat(Qt.RichText)
        self.ga_legend.setWordWrap(True)
        self.ga_legend.hide()
        tg.addWidget(self.ga_legend)

        self.tabs.addTab(tab_game, "Partie")

        # ---------------- záložka Rozbor pozice (strom zahájení + engine) ----
        tab_an = QWidget()
        ta = QVBoxLayout(tab_an)
        ta.setContentsMargins(0, 0, 0, 0)
        vsplit = QSplitter(Qt.Vertical)
        ta.addWidget(vsplit)

        tree_box = QWidget()
        tbl = QVBoxLayout(tree_box)
        tbl.setContentsMargins(6, 6, 6, 6)
        tbl.addWidget(QLabel("Strom zahájení od pozice na šachovnici"))
        tree_top = QHBoxLayout()
        tree_top.addWidget(QLabel("Za hráče:"))
        self.cmb_tree_color = self._make_color_combo(self._update_opening_tree)
        tree_top.addWidget(self.cmb_tree_color)
        tree_top.addStretch(1)
        tree_top.addWidget(QLabel("hloubka:"))
        self.spin_tree_depth = QSpinBox()
        self.spin_tree_depth.setRange(1, 20)
        self.spin_tree_depth.setValue(8)
        self.spin_tree_depth.valueChanged.connect(lambda *_: self._update_opening_tree())
        tree_top.addWidget(self.spin_tree_depth)
        tbl.addLayout(tree_top)
        self.tree_summary = QLabel("Načti PGN databázi a vyber hráče.")
        self.tree_summary.setWordWrap(True)
        self.tree_summary.setStyleSheet("color:#555;")
        tbl.addWidget(self.tree_summary)
        self.opening_tree = QTreeWidget()
        self.opening_tree.setColumnCount(5)
        self.opening_tree.setHeaderLabels(
            ["Tah", "Partií", "Winrate", "Výsledek  V / R / P", "podíl"])
        hdr = self.opening_tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, wdt in ((1, 52), (2, 52), (3, 168), (4, 56)):
            hdr.setSectionResizeMode(col, QHeaderView.Fixed)
            self.opening_tree.setColumnWidth(col, wdt)
        self.opening_tree.setItemDelegateForColumn(3, WDLBarDelegate(self.opening_tree))
        self.opening_tree.itemDoubleClicked.connect(self._on_tree_move_activated)
        self.opening_tree.itemExpanded.connect(self._on_op_item_expanded)
        tbl.addWidget(self.opening_tree, stretch=1)
        tree_hint = QLabel("Dvojklik na tah = přehraje se na velké šachovnici")
        tree_hint.setStyleSheet("color:#888;")
        tbl.addWidget(tree_hint)
        vsplit.addWidget(tree_box)

        eng_box = QWidget()
        ebl = QVBoxLayout(eng_box)
        ebl.setContentsMargins(6, 6, 6, 6)
        eng_row = QHBoxLayout()
        self.chk_engine = QCheckBox("Analýza enginem")
        self.chk_engine.toggled.connect(self._toggle_engine)
        eng_row.addWidget(self.chk_engine)
        eng_row.addWidget(QLabel("Linií:"))
        self.spin_multipv = QSpinBox()
        self.spin_multipv.setRange(1, 5)
        self.spin_multipv.setValue(2)
        self.spin_multipv.valueChanged.connect(self._on_multipv_changed)
        eng_row.addWidget(self.spin_multipv)
        self.chk_arrows = QCheckBox("Šipky")
        self.chk_arrows.setChecked(True)
        self.chk_arrows.toggled.connect(self._on_arrows_toggled)
        eng_row.addWidget(self.chk_arrows)
        eng_row.addStretch(1)
        ebl.addLayout(eng_row)

        self.engine_status = QLabel("Engine: nenastaven")
        self.engine_status.setStyleSheet("color: #666;")
        ebl.addWidget(self.engine_status)

        self.engine_lines = QListWidget()
        self.engine_lines.setMinimumHeight(84)
        self.engine_lines.setToolTip("Klikni na linii a přehraj ji na malé šachovnici níže")
        self.engine_lines.currentRowChanged.connect(self._on_line_selected)
        ebl.addWidget(self.engine_lines)

        self.var_title = QLabel("Přehrání varianty – klikni na linii výše")
        self.var_title.setStyleSheet("color: #1565c0; font-weight: bold;")
        ebl.addWidget(self.var_title)
        self.mini_board = BoardWidget(interactive=False, min_px=180)
        self.mini_board.setFixedSize(240, 240)
        mini_wrap = QHBoxLayout()
        mini_wrap.addStretch(1)
        mini_wrap.addWidget(self.mini_board)
        mini_wrap.addStretch(1)
        ebl.addLayout(mini_wrap)
        var_nav = QHBoxLayout()
        self.btn_var_start = QPushButton("⏮")
        self.btn_var_prev = QPushButton("◀")
        self.var_pos_label = QLabel("–")
        self.var_pos_label.setAlignment(Qt.AlignCenter)
        self.btn_var_next = QPushButton("▶")
        self.btn_var_end = QPushButton("⏭")
        self.btn_var_start.clicked.connect(lambda: self._var_go("start"))
        self.btn_var_prev.clicked.connect(lambda: self._var_go(-1))
        self.btn_var_next.clicked.connect(lambda: self._var_go(+1))
        self.btn_var_end.clicked.connect(lambda: self._var_go("end"))
        for wdg in (self.btn_var_start, self.btn_var_prev, self.var_pos_label,
                    self.btn_var_next, self.btn_var_end):
            var_nav.addWidget(wdg)
        ebl.addLayout(var_nav)
        self._set_variation_controls_enabled(False)
        vsplit.addWidget(eng_box)
        vsplit.setSizes([560, 320])
        self.tabs.addTab(tab_an, "Rozbor pozice")
        self.tabs.setCurrentWidget(tab_game)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([620, 560])
        self.top_tabs.addTab(board_page, "Partie a rozbor")

        # =================== karta 2: Heatmapa ===================
        self.top_tabs.addTab(self._build_heatmap_page(), "Heatmapa")

        # =================== karta 3: Zahájení ===================
        self.top_tabs.addTab(self._build_openings_page(), "Zahájení")

        # =================== karta 4: Koncovky ===================
        self.top_tabs.addTab(self._build_endgame_page(), "Koncovky")

        # =================== karta 5: Vzorce ===================
        self.top_tabs.addTab(self._build_patterns_page(), "Vzorce")

        # =================== karta 6: Přesnost ===================
        self.top_tabs.addTab(self._build_accuracy_page(), "Přesnost")

        # =================== karta 7: Grafy ===================
        self.top_tabs.addTab(self._build_charts_page(), "Grafy")

        # =================== karta 8: Taktika ===================
        self.top_tabs.addTab(self._build_tactics_page(), "Taktika")

        # =================== karta 9: Report ===================
        self.top_tabs.addTab(self._build_report_page(), "Report")
        self.top_tabs.currentChanged.connect(self._on_top_tab_changed)

        self._update_engine_status()

    def _build_filter_row(self) -> QWidget:
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(8, 2, 8, 2)
        row.addWidget(QLabel("Filtr:"))
        row.addWidget(QLabel("rok"))
        self.spin_year_from = QSpinBox()
        self.spin_year_to = QSpinBox()
        for sp in (self.spin_year_from, self.spin_year_to):
            sp.setRange(1900, 2100)
            sp.setEnabled(False)
            sp.valueChanged.connect(lambda *_: self._apply_filters())
        row.addWidget(self.spin_year_from)
        row.addWidget(QLabel("–"))
        row.addWidget(self.spin_year_to)
        row.addSpacing(12)
        row.addWidget(QLabel("tempo"))
        self.cmb_tc = QComboBox()
        self.cmb_tc.addItems(TIME_CLASSES)
        self.cmb_tc.currentIndexChanged.connect(lambda *_: self._apply_filters())
        row.addWidget(self.cmb_tc)
        row.addSpacing(12)
        row.addWidget(QLabel("soupeř"))
        self.cmb_elo = QComboBox()
        self.cmb_elo.addItems(ELO_CHOICES)
        self.cmb_elo.currentIndexChanged.connect(lambda *_: self._apply_filters())
        row.addWidget(self.cmb_elo)
        self.btn_filter_reset = QPushButton("Zrušit filtry")
        self.btn_filter_reset.clicked.connect(self._reset_filters)
        row.addWidget(self.btn_filter_reset)
        row.addStretch(1)
        self.filter_info = QLabel("")
        self.filter_info.setStyleSheet("color:#666;")
        row.addWidget(self.filter_info)
        return w

    def _reset_filters(self) -> None:
        for sp in (self.spin_year_from, self.spin_year_to):
            sp.blockSignals(True)
        self.cmb_tc.blockSignals(True)
        self.cmb_elo.blockSignals(True)
        yr = years_range(self.games) if self.games else None
        if yr:
            self.spin_year_from.setValue(yr[0])
            self.spin_year_to.setValue(yr[1])
        self.cmb_tc.setCurrentIndex(0)
        self.cmb_elo.setCurrentIndex(0)
        for sp in (self.spin_year_from, self.spin_year_to):
            sp.blockSignals(False)
        self.cmb_tc.blockSignals(False)
        self.cmb_elo.blockSignals(False)
        self._apply_filters()

    def _refresh_filter_years(self) -> None:
        yr = years_range(self.games) if self.games else None
        for sp in (self.spin_year_from, self.spin_year_to):
            sp.blockSignals(True)
            sp.setEnabled(bool(yr))
        if yr:
            self.spin_year_from.setRange(yr[0], yr[1])
            self.spin_year_to.setRange(yr[0], yr[1])
            self.spin_year_from.setValue(yr[0])
            self.spin_year_to.setValue(yr[1])
        for sp in (self.spin_year_from, self.spin_year_to):
            sp.blockSignals(False)

    def _apply_filters(self) -> None:
        player = self.cmb_player.currentData()
        yr = years_range(self.games) if self.games else None
        yf = self.spin_year_from.value() if (yr and self.spin_year_from.value() > yr[0]) else None
        yt = self.spin_year_to.value() if (yr and self.spin_year_to.value() < yr[1]) else None
        self._filter_keep = make_filter(
            player or "", year_from=yf, year_to=yt,
            tc=self.cmb_tc.currentText(), elo=self.cmb_elo.currentText())
        if self.games and self._filter_keep is not None:
            n = sum(1 for g in self.games if self._filter_keep(g))
            self.filter_info.setText(f"{n} z {len(self.games)} partií")
        else:
            self.filter_info.setText("")
        self._update_opening_tree()
        self._invalidate_analyses()

    def _build_heatmap_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        self.btn_heat_run = QPushButton("▶ Spustit rozbor")
        self.btn_heat_run.clicked.connect(lambda: self._run_analysis("heat"))
        row.addWidget(self.btn_heat_run)
        row.addWidget(QLabel("Za hráče:"))
        self.cmb_heat_color = self._make_color_combo(lambda: self._reanalyze_if_ready("heat"))
        row.addWidget(self.cmb_heat_color)
        row.addWidget(QLabel("Čí tahy:"))
        self.cmb_heat_who = QComboBox()
        self.cmb_heat_who.addItem("hráče", "player")
        self.cmb_heat_who.addItem("soupeře", "opponent")
        self.cmb_heat_who.currentIndexChanged.connect(lambda *_: self._reanalyze_if_ready("heat"))
        row.addWidget(self.cmb_heat_who)
        row.addWidget(QLabel("Pole:"))
        self.cmb_heat_mode = QComboBox()
        self.cmb_heat_mode.addItem("Kam táhne", "to")
        self.cmb_heat_mode.addItem("Odkud táhne", "from")
        self.cmb_heat_mode.addItem("Jen braní (kam)", "capture")
        self.cmb_heat_mode.currentIndexChanged.connect(lambda *_: self._reanalyze_if_ready("heat"))
        row.addWidget(self.cmb_heat_mode)
        row.addWidget(QLabel("Figura:"))
        self.cmb_heat_piece = QComboBox()
        for lbl, val in _PIECE_CHOICES:
            self.cmb_heat_piece.addItem(lbl, val)
        self.cmb_heat_piece.currentIndexChanged.connect(lambda *_: self._reanalyze_if_ready("heat"))
        row.addWidget(self.cmb_heat_piece)
        row.addStretch(1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Fáze:"))
        self.cmb_heat_phase = QComboBox()
        for lbl, val in (("celá partie", "all"), ("zahájení (1–15)", "opening"),
                         ("střední hra (16–40)", "middle"), ("koncovka (41+)", "endgame")):
            self.cmb_heat_phase.addItem(lbl, val)
        self.cmb_heat_phase.currentIndexChanged.connect(lambda *_: self._reanalyze_if_ready("heat"))
        row2.addWidget(self.cmb_heat_phase)
        row2.addWidget(QLabel("Partie:"))
        self.cmb_heat_outcome = QComboBox()
        for lbl, val in (("všechny", "all"), ("jen výhry", "win"), ("jen prohry", "loss")):
            self.cmb_heat_outcome.addItem(lbl, val)
        self.cmb_heat_outcome.currentIndexChanged.connect(
            lambda *_: self._reanalyze_if_ready("heat"))
        row2.addWidget(self.cmb_heat_outcome)
        self.chk_heat_black = QCheckBox("Pohled černého")
        self.chk_heat_black.toggled.connect(lambda *_: self._reanalyze_if_ready("heat"))
        row2.addWidget(self.chk_heat_black)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.heatmap = HeatmapWidget()
        lay.addWidget(self.heatmap, stretch=1)
        self.heat_note = QLabel("Načti PGN databázi a vyber hráče.")
        self.heat_note.setWordWrap(True)
        self.heat_note.setStyleSheet("color:#666;")
        lay.addWidget(self.heat_note)
        return page

    def _build_openings_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        self.btn_op_run = QPushButton("▶ Spustit rozbor")
        self.btn_op_run.clicked.connect(lambda: self._run_analysis("op"))
        row.addWidget(self.btn_op_run)
        row.addWidget(QLabel("Za hráče:"))
        self.cmb_op_color = self._make_color_combo(lambda: self._reanalyze_if_ready("op"))
        row.addWidget(self.cmb_op_color)
        row.addStretch(1)
        lay.addLayout(row)
        self.op_summary = QLabel("Načti PGN databázi a vyber hráče.")
        self.op_summary.setWordWrap(True)
        self.op_summary.setStyleSheet("color:#555;")
        lay.addWidget(self.op_summary)
        self.opening_stats_tree = QTreeWidget()
        self.opening_stats_tree.setColumnCount(4)
        self.opening_stats_tree.setHeaderLabels(
            ["ECO kód / varianta / partie", "Partií", "Winrate", "Výsledek  V / R / P"])
        oh = self.opening_stats_tree.header()
        oh.setStretchLastSection(False)
        oh.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, wdt in ((1, 60), (2, 60), (3, 180)):
            oh.setSectionResizeMode(col, QHeaderView.Fixed)
            self.opening_stats_tree.setColumnWidth(col, wdt)
        self.opening_stats_tree.setItemDelegateForColumn(3, WDLBarDelegate(self.opening_stats_tree))
        self.opening_stats_tree.setSortingEnabled(True)
        self.opening_stats_tree.sortByColumn(0, Qt.AscendingOrder)
        self.opening_stats_tree.setToolTip("Klikni na hlavičku sloupce pro řazení")
        self.opening_stats_tree.itemDoubleClicked.connect(self._on_opening_item_activated)
        lay.addWidget(self.opening_stats_tree, stretch=1)
        hint = QLabel("Partie hráče rozřazené podle zahájení – bere se ECO kód a název "
                      "z hlavičky PGN, a když chybí, zařadí se podle úvodních tahů. "
                      "Strom má tři úrovně: skupina ECO (A–E) → konkrétní zahájení → partie. "
                      "Dvojklik na partii ji přehraje od konce teoretické linie zahájení. "
                      "Rozbor se spustí až tlačítkem.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        lay.addWidget(hint)
        return page

    def _build_endgame_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        self.btn_eg_run = QPushButton("▶ Spustit rozbor")
        self.btn_eg_run.clicked.connect(lambda: self._run_analysis("eg"))
        row.addWidget(self.btn_eg_run)
        row.addStretch(1)
        lay.addLayout(row)
        self.eg_summary = QLabel("Načti PGN databázi a vyber hráče.")
        self.eg_summary.setWordWrap(True)
        self.eg_summary.setStyleSheet("color:#555;")
        lay.addWidget(self.eg_summary)
        self.endgame_tree = QTreeWidget()
        self.endgame_tree.setColumnCount(4)
        self.endgame_tree.setHeaderLabels(
            ["Kategorie / počet pěšců / partie", "Partií", "Winrate", "Výsledek  V / R / P"])
        eh = self.endgame_tree.header()
        eh.setStretchLastSection(False)
        eh.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, wdt in ((1, 60), (2, 60), (3, 180)):
            eh.setSectionResizeMode(col, QHeaderView.Fixed)
            self.endgame_tree.setColumnWidth(col, wdt)
        self.endgame_tree.setItemDelegateForColumn(3, WDLBarDelegate(self.endgame_tree))
        self.endgame_tree.setSortingEnabled(True)
        self.endgame_tree.sortByColumn(0, Qt.AscendingOrder)
        self.endgame_tree.setToolTip("Klikni na hlavičku sloupce pro řazení")
        self.endgame_tree.itemDoubleClicked.connect(self._on_endgame_item_activated)
        lay.addWidget(self.endgame_tree, stretch=1)
        hint = QLabel("Koncovka = pozice, kde má každá strana ≤ 13 bodů materiálu (bez krále) "
                      "nebo jsou na šachovnici ≤ 4 figury mimo krále a pěšce; počítají se jen "
                      "pozice s rozdílem materiálu do 4 bodů – výjimkou jsou pěšcové koncovky "
                      "(jen král a pěšci), které se počítají vždy. "
                      "Kategorie = typ koncovky podle druhů figur + přesný soupis figur obou "
                      "stran (kolik věží / dam / střelců / jezdců, u střelcovek stejná/opačná "
                      "barva), nezávisle na barvě hráče; počítá se pro obě barvy. "
                      "Uvnitř kategorie jsou partie rozděleny podle počtu pěšců ve chvíli, kdy "
                      "do kategorie vstoupily (každá partie právě jednou). "
                      "Dvojklik na partii ji přehraje od chvíle, kdy se koncovkou stala.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        lay.addWidget(hint)
        return page

    def _build_patterns_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        self.btn_pat_run = QPushButton("▶ Spustit rozbor")
        self.btn_pat_run.clicked.connect(lambda: self._run_analysis("pat"))
        row.addWidget(self.btn_pat_run)
        row.addWidget(QLabel("Za hráče:"))
        self.cmb_pat_color = self._make_color_combo(lambda: self._reanalyze_if_ready("pat"))
        row.addWidget(self.cmb_pat_color)
        row.addStretch(1)
        self.btn_anomaly = QPushButton("🔍 Nejpodivnější partie")
        self.btn_anomaly.setToolTip("Partie nejméně podobné tvé běžné hře "
                                    "(Mahalanobisova vzdálenost ve feature space).")
        self.btn_anomaly.clicked.connect(self._open_anomaly)
        row.addWidget(self.btn_anomaly)
        lay.addLayout(row)
        self.pat_summary = QLabel("Načti PGN databázi a vyber hráče.")
        self.pat_summary.setWordWrap(True)
        self.pat_summary.setStyleSheet("color:#555;")
        lay.addWidget(self.pat_summary)
        self.patterns_tree = QTreeWidget()
        self.patterns_tree.setColumnCount(4)
        self.patterns_tree.setHeaderLabels(
            ["Rozbor", "Partií", "Winrate", "Výsledek  V / R / P"])
        ph = self.patterns_tree.header()
        ph.setStretchLastSection(False)
        ph.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, wdt in ((1, 60), (2, 60), (3, 180)):
            ph.setSectionResizeMode(col, QHeaderView.Fixed)
            self.patterns_tree.setColumnWidth(col, wdt)
        self.patterns_tree.setItemDelegateForColumn(3, WDLBarDelegate(self.patterns_tree))
        lay.addWidget(self.patterns_tree, stretch=1)
        hint = QLabel("Partie vybraného hráče rozdělené podle různých kritérií; u každého "
                      "koše je počet partií, winrate a pruh V / R / P. Winrate = výhry ÷ "
                      "rozhodnuté partie (remízy se do čitatele nepočítají). Pěšcová struktura "
                      "se hodnotí ze snímku pozice kolem 20. tahu. Respektuje se filtr databáze. "
                      "Rozbor se spustí až tlačítkem.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        lay.addWidget(hint)
        return page

    def _build_accuracy_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        self.btn_acc_run = QPushButton("▶ Spustit rozbor")
        self.btn_acc_run.clicked.connect(self._toggle_accuracy_batch)
        row.addWidget(self.btn_acc_run)
        row.addWidget(QLabel("Za hráče:"))
        self.cmb_acc_color = self._make_color_combo(lambda: None)
        row.addWidget(self.cmb_acc_color)
        row.addWidget(QLabel("hloubka:"))
        self.spin_acc_depth = QSpinBox()
        self.spin_acc_depth.setRange(8, 18)
        self.spin_acc_depth.setValue(int(self.config.get("acc_depth", 12)))
        row.addWidget(self.spin_acc_depth)
        row.addWidget(QLabel("max partií:"))
        self.spin_acc_cap = QSpinBox()
        self.spin_acc_cap.setRange(5, 1_000_000)
        self.spin_acc_cap.setSingleStep(25)
        self.spin_acc_cap.setValue(int(self.config.get("acc_cap", 30)))
        self.spin_acc_cap.setToolTip(
            "Rozebere se tolik nejnovějších partií hráče (po filtru). Nastav vysoko "
            "= všechny. Pozor: rozbor enginem je pomalý, u tisíců partií i s víc "
            "enginy počítej v desítkách minut (výsledky se ale cachují).")
        row.addWidget(self.spin_acc_cap)
        self.chk_acc_thorough = QCheckBox("důkladný rozbor (ostrost, komplexita; ~2× pomalejší)")
        self.chk_acc_thorough.setChecked(bool(self.config.get("acc_thorough", False)))
        row.addWidget(self.chk_acc_thorough)
        row.addStretch(1)
        lay.addLayout(row)

        self.acc_progress = QProgressBar()
        self.acc_progress.setVisible(False)
        lay.addWidget(self.acc_progress)
        self.acc_summary = QLabel("Načti PGN databázi a vyber hráče.")
        self.acc_summary.setWordWrap(True)
        self.acc_summary.setStyleSheet("color:#555;")
        lay.addWidget(self.acc_summary)

        self.accuracy_tree = QTreeWidget()
        self.accuracy_tree.setColumnCount(14)
        self.accuracy_tree.setHeaderLabels(
            ["Ukazatel / skupina", "Partií", "Přesnost %", "ACPL",
             "EP/partii", "Hrubky/100", "T1 %", "Index", "IPR",
             "Volatilita %", "Obraty", "Ostrost", "Komplex.", "Takt. %"])
        ah = self.accuracy_tree.header()
        ah.setStretchLastSection(False)
        ah.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, wdt in ((1, 52), (2, 78), (3, 58), (4, 78), (5, 78),
                         (6, 58), (7, 58), (8, 58),
                         (9, 84), (10, 58), (11, 62), (12, 70), (13, 58)):
            ah.setSectionResizeMode(col, QHeaderView.Fixed)
            self.accuracy_tree.setColumnWidth(col, wdt)
        hi = self.accuracy_tree.headerItem()
        hi.setToolTip(7, "Kompozitní index přesnosti 0–100: 45 % přesnost, "
                         "30 % ACPL, 15 % chybovost, 10 % shoda s enginem.")
        hi.setToolTip(8, "Odhad výkonnosti. Ukotvený na tvé průměrné Elo v rozboru; "
                         "měří, o kolik jsi v dané skupině hrál nad/pod svou úroveň "
                         "podle kvality tahů. Jedna partie hodně kolísá (odchylka "
                         "omezená na ±450); vypovídající jsou řádky po barvě a období.")
        hi.setToolTip(9, "Volatilita: průměrná změna šance na výhru na jeden půltah "
                         "(z hodnocení enginu). Počítá se pro každý půltah zvlášť a "
                         "za partii se bere průměr – čím vyšší, tím divočejší průběh.")
        hi.setToolTip(10, "Obraty: kolikrát za partii se prohodilo, kdo stojí líp "
                          "(šance na výhru překročí 50 %). Součet za celou partii.")
        hi.setToolTip(11, "Ostrost pozic (0–1): z top-3 linií enginu podíl tahů, které "
                          "NEudrží pozici (0 = drží skoro každý tah, →1 = drží jediný). "
                          "Počítá se v každé pozici hráče na tahu, za partii průměr. "
                          "Vyžaduje „důkladný rozbor“.")
        hi.setToolTip(12, "Komplexita pozic: rozptyl hodnocení top-3 tahů. Vysoká = "
                          "snadno se seknout. Per pozice, za partii průměr. "
                          "Vyžaduje „důkladný rozbor“.")
        hi.setToolTip(13, "Tactical Awareness: v pozicích s jasně nejlepším tahem "
                          "(kritičnost ≥ 0,15) podíl, kde hráč trefil tah enginu. "
                          "Per pozice, za partii poměr. Vyžaduje „důkladný rozbor“.")
        self.accuracy_tree.itemDoubleClicked.connect(self._on_accuracy_item_activated)
        lay.addWidget(self.accuracy_tree, stretch=1)
        hint = QLabel(
            "Engine projde vybrané partie hráče (od nejnovějších) a spočítá: "
            "přesnost (lichess vzorec z pravděpodobnosti výhry), ACPL (setiny pěšce), "
            "ztrátu v očekávaných bodech (EP), hrubky na 100 tahů, shodu s nejlepším "
            "tahem enginu (T1, po konci teorie a bez vynucených tahů), "
            "kompozitní index přesnosti (0–100), IPR (odhad výkonnosti), charakter "
            "partií (volatilita, vstup do koncovky) a dotahování (kolik z vyhraných "
            "pozic doopravdy vyhraješ, kolik z prohraných zachráníš). „Důkladný rozbor“ "
            "navíc spočítá ostrost a komplexitu pozic a kritičností vážený ACPL (multipv, "
            "~2× pomalejší). Výsledky se ukládají do cache (analysis_cache.json.gz), další "
            "spuštění je pak rychlé. Respektuje filtr databáze. Dvojklik na partii ji otevře.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        lay.addWidget(hint)
        return page

    _CHART_KINDS = [
        ("Eval graf vybrané partie", "eval"),
        ("Vývoj v čase (karta Přesnost)", "trend"),
        ("Winrate podle zahájení", "openings"),
        ("Divokost partií (histogram)", "volatility"),
        ("Kumulativní „štěstí“", "luck"),
        ("Délka partie (histogram)", "length"),
        ("První braní (histogram)", "first_capture"),
        ("Vstup do koncovky (histogram)", "endgame_entry"),
        ("Materiálové manko (histogram)", "material"),
        ("Elo rozdíl soupeře (histogram)", "elo"),
        ("Délka partie podle výsledku", "length_by_result"),
        ("Dotahování: konverze / záchrana", "conversion"),
        ("Ztráta bodů z vyhraných pozic (histogram)", "ep_wasted"),
        ("Tactical Awareness (histogram)", "tactical"),
        ("Profil hráče (radar)", "radar"),
        ("Kritičnost × přesnost tahu", "crit_scatter"),
        ("Přesnost podle figury / typu tahu", "piece_acc"),
        ("Hrubky podle figury / typu tahu", "piece_blund"),
        ("Chybová heatmapa – odkud táhnu", "err_from"),
        ("Chybová heatmapa – kam táhnu", "err_to"),
        ("Heatmapa winrate podle dne a hodiny", "time_heatmap"),
        ("Tahy podle kategorie chess.com – 1 partie (koláč)", "move_pie"),
        ("Tahy podle kategorie chess.com – celý rozbor (koláč)", "move_pie_db"),
        ("Elo hráče × Elo soupeře (scatter)", "opponent_scatter"),
    ]
    _DIST_KINDS = {"length", "first_capture", "endgame_entry", "material", "elo",
                   "length_by_result"}
    _COLOR_KINDS = _DIST_KINDS | {"openings", "luck", "time_heatmap", "opponent_scatter"}
    # bezpečnostní strop, ne kosmetický vzorek – kreslí se VŽDY všechny partie,
    def _build_charts_page(self) -> QWidget:
        page = QWidget()
        self._charts_page = page
        lay = QVBoxLayout(page)
        row = QHBoxLayout()
        row.addWidget(QLabel("Graf:"))
        self.cmb_chart_kind = QComboBox()
        for lbl, val in self._CHART_KINDS:
            self.cmb_chart_kind.addItem(lbl, val)
        self.cmb_chart_kind.currentIndexChanged.connect(lambda *_: self._update_chart())
        row.addWidget(self.cmb_chart_kind)
        row.addWidget(QLabel("Ukazatel:"))
        self.cmb_chart_metric = QComboBox()
        for lbl, val in (("ACPL", "acpl"), ("Přesnost %", "accuracy"), ("IPR", "ipr"),
                        ("Konverze %", "conversion"), ("Záchrana %", "resourcefulness"),
                        ("Tactical Aw. %", "tact")):
            self.cmb_chart_metric.addItem(lbl, val)
        self.cmb_chart_metric.currentIndexChanged.connect(lambda *_: self._update_chart())
        self.cmb_chart_metric.setVisible(False)
        row.addWidget(self.cmb_chart_metric)
        self.cmb_chart_color = self._make_color_combo(lambda: self._update_chart())
        self.cmb_chart_color.setVisible(False)
        row.addWidget(self.cmb_chart_color)
        self.btn_chart_refresh = QPushButton("↻ Překreslit")
        self.btn_chart_refresh.clicked.connect(self._update_chart)
        row.addWidget(self.btn_chart_refresh)
        row.addStretch(1)
        lay.addLayout(row)

        self.chart_note = QLabel("")
        self.chart_note.setWordWrap(True)
        self.chart_note.setStyleSheet("color:#666;")
        lay.addWidget(self.chart_note)

        self.chart_stack = QStackedWidget()
        self.chart_view = QChartView()
        self.chart_view.setRenderHint(QPainter.Antialiasing)
        self.chart_hist = HistogramChart()
        self.chart_heat = TimeHeatmap()
        self.chart_errmap = HeatmapWidget()
        self.chart_stack.addWidget(self.chart_view)
        self.chart_stack.addWidget(self.chart_hist)
        self.chart_stack.addWidget(self.chart_heat)
        self.chart_stack.addWidget(self.chart_errmap)
        lay.addWidget(self.chart_stack, stretch=1)
        return page

    def _on_top_tab_changed(self, index: int) -> None:
        if hasattr(self, "_charts_page") and self.top_tabs.widget(index) is self._charts_page:
            self._update_chart()
        if hasattr(self, "_tactics_page") and self.top_tabs.widget(index) is self._tactics_page \
                and not self._tactics_all:
            self._find_tactics()

    # ==================================================================== Taktika
    _TAC_WHO = [("hráč i soupeř", "both"), ("jen hráč", "player"), ("jen soupeř", "opp")]
    _TAC_TYPE = [("vše", "all"), ("přehlédnuté", "missed"), ("nalezené", "found")]
    _TAC_STATE = [("vše", "all"), ("neviděné", "unseen"), ("viděné (nevyřešené)", "seen"),
                  ("vyřešené správně", "solved_ok"), ("vyřešené špatně", "solved_fail")]

    def _build_tactics_page(self) -> QWidget:
        page = QWidget()
        self._tactics_page = page
        lay = QVBoxLayout(page)

        row = QHBoxLayout()
        row.addWidget(QLabel("Za hráče:"))
        self.cmb_tac_color = self._make_color_combo(self._apply_tactics_filter)
        row.addWidget(self.cmb_tac_color)
        row.addWidget(QLabel("Čí tah:"))
        self.cmb_tac_who = QComboBox()
        for lbl, val in self._TAC_WHO:
            self.cmb_tac_who.addItem(lbl, val)
        self.cmb_tac_who.currentIndexChanged.connect(lambda *_: self._apply_tactics_filter())
        row.addWidget(self.cmb_tac_who)
        row.addWidget(QLabel("Typ:"))
        self.cmb_tac_type = QComboBox()
        for lbl, val in self._TAC_TYPE:
            self.cmb_tac_type.addItem(lbl, val)
        self.cmb_tac_type.currentIndexChanged.connect(lambda *_: self._apply_tactics_filter())
        row.addWidget(self.cmb_tac_type)
        row.addWidget(QLabel("Stav:"))
        self.cmb_tac_state = QComboBox()
        for lbl, val in self._TAC_STATE:
            self.cmb_tac_state.addItem(lbl, val)
        self.cmb_tac_state.currentIndexChanged.connect(lambda *_: self._apply_tactics_filter())
        row.addWidget(self.cmb_tac_state)
        self.chk_tac_star = QCheckBox("jen ★")
        self.chk_tac_star.setToolTip("Zobrazit jen úlohy označené hvězdičkou (obzvlášť pěkné).")
        self.chk_tac_star.stateChanged.connect(lambda *_: self._apply_tactics_filter())
        row.addWidget(self.chk_tac_star)
        self.btn_tac_find = QPushButton("↻ Najít úlohy")
        self.btn_tac_find.clicked.connect(self._find_tactics)
        row.addWidget(self.btn_tac_find)
        row.addStretch(1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Motiv:"))
        self.cmb_tac_motif = QComboBox()
        self.cmb_tac_motif.addItem("vše", "all")
        for k in MOTIF_ORDER:
            self.cmb_tac_motif.addItem(MOTIF_LABEL[k], k)
        self.cmb_tac_motif.currentIndexChanged.connect(lambda *_: self._apply_tactics_filter())
        row2.addWidget(self.cmb_tac_motif)
        row2.addWidget(QLabel("Obtížnost:"))
        self.cmb_tac_diff = QComboBox()
        for lbl, lo, hi in (("vše", 0, 9999), ("do 1300", 0, 1300), ("1300–1700", 1300, 1700),
                            ("1700–2100", 1700, 2100), ("nad 2100", 2100, 9999)):
            self.cmb_tac_diff.addItem(lbl, (lo, hi))
        self.cmb_tac_diff.currentIndexChanged.connect(lambda *_: self._apply_tactics_filter())
        row2.addWidget(self.cmb_tac_diff)
        self.chk_tac_due = QCheckBox("jen k opakování dnes")
        self.chk_tac_due.setToolTip("Úlohy naplánované systémem opakování (SM-2) na dnešek "
                                    "nebo dřív. Řeš je a ohodnoť – interval se posune.")
        self.chk_tac_due.stateChanged.connect(lambda *_: self._apply_tactics_filter())
        row2.addWidget(self.chk_tac_due)
        self.btn_tac_review = QPushButton("▶ Trénink opakování")
        self.btn_tac_review.clicked.connect(self._start_tactics_review)
        row2.addWidget(self.btn_tac_review)
        row2.addStretch(1)
        lay.addLayout(row2)

        self.tac_summary = QLabel("Rozeber partie na kartě Přesnost (ideálně „důkladný "
                                  "rozbor“), pak se tu objeví taktické úlohy.")
        self.tac_summary.setWordWrap(True)
        self.tac_summary.setStyleSheet("color:#555;")
        lay.addWidget(self.tac_summary)

        split = QSplitter()
        self.tac_table = QTableWidget(0, 10)
        self.tac_table.setHorizontalHeaderLabels(
            ["Partie", "Tah", "Kdo", "Typ", "Δ", "Stav", "Užit.", "★", "Motiv", "Obtíž."])
        self.tac_table.verticalHeader().setVisible(False)
        self.tac_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tac_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.tac_table.setSelectionMode(QTableWidget.SingleSelection)
        self.tac_table.horizontalHeader().setStretchLastSection(False)
        self.tac_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c, wd in ((1, 40), (2, 54), (3, 76), (4, 46), (5, 128), (6, 44), (7, 30),
                      (8, 150), (9, 52)):
            self.tac_table.setColumnWidth(c, wd)
        self.tac_table.itemSelectionChanged.connect(self._on_tactic_selected)
        self.tac_table.itemDoubleClicked.connect(self._on_tactic_double_clicked)
        split.addWidget(self.tac_table)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.tac_board = BoardWidget(interactive=True, min_px=300)
        self.tac_board.user_move.connect(self._on_tactic_move)
        rl.addWidget(self.tac_board, stretch=1)
        self.tac_prompt = QLabel("Vyber úlohu ze seznamu.")
        self.tac_prompt.setWordWrap(True)
        self.tac_prompt.setStyleSheet("font-weight:bold;")
        rl.addWidget(self.tac_prompt)
        self.tac_feedback = QLabel("")
        self.tac_feedback.setWordWrap(True)
        rl.addWidget(self.tac_feedback)
        brow = QHBoxLayout()
        self.btn_tac_hint = QPushButton("💡 Nápověda")
        self.btn_tac_hint.setToolTip("Prozradí taktický motiv úlohy.")
        self.btn_tac_hint.clicked.connect(self._tactic_hint)
        brow.addWidget(self.btn_tac_hint)
        self.btn_tac_solution = QPushButton("Ukázat řešení")
        self.btn_tac_solution.clicked.connect(self._tactic_show_solution)
        brow.addWidget(self.btn_tac_solution)
        self.btn_tac_next = QPushButton("Další ▶")
        self.btn_tac_next.clicked.connect(self._tactic_next_unseen)
        brow.addWidget(self.btn_tac_next)
        self.btn_tac_star = QPushButton("☆ Označit jako pěknou")
        self.btn_tac_star.setToolTip("Označit obzvlášť pěknou úlohu hvězdičkou.")
        self.btn_tac_star.clicked.connect(self._tactic_toggle_star)
        brow.addWidget(self.btn_tac_star)
        rl.addLayout(brow)
        urow = QHBoxLayout()
        urow.addWidget(QLabel("Užitečná úloha?"))
        self.btn_tac_useful = QPushButton("👍 ano")
        self.btn_tac_useful.clicked.connect(lambda: self._tactic_rate(True))
        urow.addWidget(self.btn_tac_useful)
        self.btn_tac_useless = QPushButton("👎 ne")
        self.btn_tac_useless.clicked.connect(lambda: self._tactic_rate(False))
        urow.addWidget(self.btn_tac_useless)
        urow.addStretch(1)
        rl.addLayout(urow)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([560, 420])
        lay.addWidget(split, stretch=1)
        self._set_tactic_controls_enabled(False)
        return page

    def _set_tactic_controls_enabled(self, on: bool) -> None:
        for b in (self.btn_tac_solution, self.btn_tac_next, self.btn_tac_useful,
                  self.btn_tac_useless, self.btn_tac_star, self.btn_tac_hint):
            b.setEnabled(on)

    def _tactic_hint(self) -> None:
        p = self._tactics_cur
        if p is None:
            return
        if p.motifs:
            self.tac_feedback.setText("💡 Motiv: " + motif_labels(p.motifs))
        else:
            b = chess.Board(p.fen)
            mv = chess.Move.from_uci(p.solution)
            kind = ("braní" if b.is_capture(mv) else "šach" if b.gives_check(mv)
                    else "proměna" if mv.promotion else "tichý tah")
            self.tac_feedback.setText(f"💡 Žádný jasný motiv – řešení je {kind}.")

    def _update_star_button(self, on: bool) -> None:
        self.btn_tac_star.setText("★ Pěkná (označeno)" if on else "☆ Označit jako pěknou")

    def _tactic_toggle_star(self) -> None:
        p = self._tactics_cur
        if p is None:
            return
        now = self._tactics_store.toggle_starred(p.pid, p)
        self._update_star_button(now)
        if self.chk_tac_star.isChecked() and not now:
            self._apply_tactics_filter()      # vypadne z filtrovaného seznamu
        else:
            self._refresh_tactic_row(p)
        self._update_tactics_summary()

    def _find_tactics(self) -> None:
        if self._tactics_extractor is not None and self._tactics_extractor.isRunning():
            return
        player = self.cmb_player.currentData()
        if not self.games or not player:
            self.tac_summary.setText("Načti PGN databázi a vyber hráče.")
            return
        cache = self._acc_cache
        if cache is None:
            cache = self._acc_cache = AnalysisCache()
        depth = int(self.config.get("acc_depth", 12))
        self.tac_summary.setText("Hledám úlohy v rozboru… (běží na pozadí, u velké "
                                 "databáze to chvíli trvá)")
        self.btn_tac_find.setEnabled(False)
        self._tactics_extractor = TacticsExtractor(
            self.games, player, self._filter_keep, cache, depth, parent=self,
            use_wdl=self._use_wdl())
        self._tactics_extractor.done.connect(self._on_tactics_extracted)
        self._tactics_extractor.failed.connect(self._on_tactics_extract_failed)
        self._tactics_extractor.finished.connect(
            lambda: setattr(self, "_tactics_extractor", None))
        self._tactics_extractor.start()

    def _on_tactics_extracted(self, puzzles: list) -> None:
        self._tactics_all = puzzles
        self.btn_tac_find.setEnabled(True)
        self._apply_tactics_filter()

    def _on_tactics_extract_failed(self, msg: str) -> None:
        self.btn_tac_find.setEnabled(True)
        self.tac_summary.setText(f"Chyba při hledání úloh: {msg}")

    def _refresh_tactics_after_analysis(self) -> None:
        if hasattr(self, "_tactics_page") and self.top_tabs.currentWidget() is self._tactics_page:
            self._find_tactics()
        else:
            self._tactics_all = []   # přinutí přenačíst při dalším otevření karty

    def _apply_tactics_filter(self) -> None:
        if not hasattr(self, "tac_table"):
            return
        col = self.cmb_tac_color.currentData()
        who = self.cmb_tac_who.currentData()
        typ = self.cmb_tac_type.currentData()
        state = self.cmb_tac_state.currentData()
        star_only = self.chk_tac_star.isChecked()
        motif = self.cmb_tac_motif.currentData()
        dlo, dhi = self.cmb_tac_diff.currentData()
        due_only = self.chk_tac_due.isChecked()
        player = self.cmb_player.currentData()

        def keep(p) -> bool:
            g = self.games[p.gi] if 0 <= p.gi < len(self.games) else None
            if g is not None and col != "both":
                is_white = (g.headers.get("White", "").strip() == player)
                if (col == "white") != is_white:
                    return False
            if who == "player" and not p.mover_is_player:
                return False
            if who == "opp" and p.mover_is_player:
                return False
            if typ == "missed" and p.found:
                return False
            if typ == "found" and not p.found:
                return False
            if state != "all" and self._tactics_store.status(p.pid) != state:
                return False
            if star_only and not self._tactics_store.starred(p.pid):
                return False
            if motif != "all" and motif not in (p.motifs or []):
                return False
            if not (dlo <= p.difficulty < dhi):
                return False
            if due_only and not self._tactics_store.is_due(p.pid):
                return False
            return True

        shown = [p for p in self._tactics_all if keep(p)]
        if due_only:
            shown.sort(key=lambda p: (self._tactics_store.due_date(p.pid) or _date.max))
        self._tactics_shown = shown
        self._fill_tactics_table()
        self._update_tactics_summary()

    def _fill_tactics_table(self) -> None:
        t = self.tac_table
        t.blockSignals(True)
        t.setRowCount(len(self._tactics_shown))
        for i, p in enumerate(self._tactics_shown):
            st = self._tactics_store.status(p.pid)
            us = self._tactics_store.useful(p.pid)
            due_mark = " ⏰" if self._tactics_store.is_due(p.pid) else ""
            cells = [
                p.label, str(p.move_no),
                "hráč" if p.mover_is_player else "soupeř",
                "našel" if p.found else "přehlédl",
                f"{p.swing:.0f}",
                STATUS_LABEL.get(st, st) + due_mark,
                {True: "👍", False: "👎"}.get(us, "–"),
                "★" if self._tactics_store.starred(p.pid) else "",
                motif_labels(p.motifs) if p.motifs else "–",
                str(p.difficulty),
            ]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                if c > 0:
                    it.setTextAlignment(Qt.AlignCenter)
                if c == 4 and st == "solved_ok":
                    it.setForeground(QColor("#2e7d32"))
                if c == 7:
                    it.setForeground(QColor("#f9a825"))
                if c == 9:
                    it.setForeground(QColor("#c62828" if p.difficulty >= 2000
                                            else "#e65100" if p.difficulty >= 1600
                                            else "#2e7d32"))
                t.setItem(i, c, it)
        t.blockSignals(False)

    def _on_tactic_selected(self) -> None:
        rows = self.tac_table.selectionModel().selectedRows()
        if not rows:
            return
        r = rows[0].row()
        if 0 <= r < len(self._tactics_shown):
            self._load_tactic(self._tactics_shown[r])

    def _on_tactic_double_clicked(self, item) -> None:
        r = item.row()
        if 0 <= r < len(self._tactics_shown):
            p = self._tactics_shown[r]
            self._jump_to_game(p.gi, max(0, p.ply - 1))

    def _load_tactic(self, p) -> None:
        self._tactics_cur = p
        self._tactics_attempted = False
        self._tactics_wrong = 0
        board = chess.Board(p.fen)
        if self.tac_board.orientation != board.turn:
            self.tac_board.flip()
        self.tac_board.set_arrows([])
        self.tac_board.set_position(board, None)
        self.tac_board.set_interactive(True)
        side = "bílý" if board.turn == chess.WHITE else "černý"
        whose = "Hráč" if p.mover_is_player else "Soupeř"
        ctx = (f"{whose} ho v partii NAŠEL – zkus si to zopakovat."
               if p.found else
               f"{whose} ho v partii PŘEHLÉDL (stálo to ≈ {p.swing:.0f} % šance na výhru).")
        rev = ""
        if self._tactics_store.is_due(p.pid):
            rev = "  ⏰ opakování"
        self.tac_prompt.setText(
            f"Na tahu je {side} – najdi nejlepší tah (obtížnost ≈ {p.difficulty}). "
            f"{ctx}{rev}")
        self.tac_feedback.setText("")
        self._set_tactic_controls_enabled(True)
        self._update_star_button(self._tactics_store.starred(p.pid))
        self._tactics_store.mark_seen(p.pid, p)
        self._refresh_tactic_row(p)
        self._update_tactics_summary()

    def _refresh_tactic_row(self, p) -> None:
        try:
            i = self._tactics_shown.index(p)
        except ValueError:
            return
        st = self._tactics_store.status(p.pid)
        us = self._tactics_store.useful(p.pid)
        due = " ⏰" if self._tactics_store.is_due(p.pid) else ""
        if self.tac_table.item(i, 5):
            self.tac_table.item(i, 5).setText(STATUS_LABEL.get(st, st) + due)
        if self.tac_table.item(i, 6):
            self.tac_table.item(i, 6).setText({True: "👍", False: "👎"}.get(us, "–"))
        if self.tac_table.item(i, 7):
            self.tac_table.item(i, 7).setText("★" if self._tactics_store.starred(p.pid) else "")

    def _on_tactic_move(self, move) -> None:
        p = self._tactics_cur
        if p is None:
            return
        accept = p.accept or [p.solution]
        ok = move.uci() in accept
        if ok and not self._tactics_attempted:
            q = 5 if self._tactics_wrong == 0 else 3 if self._tactics_wrong == 1 else 2
            self._tactics_store.mark_solved(p.pid, True, p, quality=q)
            self._tactics_attempted = True
        elif not ok:
            self._tactics_wrong += 1
        if ok:
            extra = ""
            if move.uci() != p.solution:
                b = chess.Board(p.fen)
                try:
                    extra = f" (engine dává {b.san(chess.Move.from_uci(p.solution))}, " \
                            f"tvůj tah je stejně dobrý)"
                except Exception:
                    extra = ""
            self.tac_feedback.setText(f"✓ Správně{extra}.")
            self.tac_board.set_arrows([chess.svg.Arrow(move.from_square, move.to_square,
                                                       color="#2e7d32")])
            self.tac_board.set_interactive(False)
        else:
            self.tac_feedback.setText("✗ Ne. Zkus to znovu, nebo si nech ukázat řešení.")
            b = chess.Board(p.fen)
            self.tac_board.set_position(b, None)   # vrátí pozici, ať zkusí znovu
        self._refresh_tactic_row(p)
        self._update_tactics_summary()

    def _tactic_show_solution(self) -> None:
        p = self._tactics_cur
        if p is None:
            return
        if not self._tactics_attempted:
            q = 1 if self._tactics_wrong == 0 else 2
            self._tactics_store.mark_solved(p.pid, False, p, quality=q)
            self._tactics_attempted = True
        mv = chess.Move.from_uci(p.solution)
        b = chess.Board(p.fen)
        self.tac_board.set_position(b, None)
        self.tac_board.set_arrows([chess.svg.Arrow(mv.from_square, mv.to_square, color="#1565c0")])
        self.tac_board.set_interactive(False)
        try:
            san = b.san(mv)
        except Exception:
            san = p.solution
        if p.found:
            tail = "– přesně tak to v partii padlo."
        else:
            try:
                played_san = b.san(chess.Move.from_uci(p.played))
            except Exception:
                played_san = p.played
            tail = f"– v partii místo toho padlo {played_san}."
        self.tac_feedback.setText(f"Řešení: {san}  {tail}")
        self._refresh_tactic_row(p)
        self._update_tactics_summary()

    def _tactic_next_unseen(self) -> None:
        start = 0
        if self._tactics_cur in self._tactics_shown:
            start = self._tactics_shown.index(self._tactics_cur) + 1
        order = list(range(start, len(self._tactics_shown))) + list(range(0, start))
        review = self.chk_tac_due.isChecked()
        for i in order:
            p = self._tactics_shown[i]
            hit = (self._tactics_store.is_due(p.pid) if review
                   else self._tactics_store.status(p.pid) == "unseen")
            if hit and p is not self._tactics_cur:
                self.tac_table.selectRow(i)
                return
        self.tac_feedback.setText(
            "Hotovo – žádná další úloha k opakování." if review
            else "Žádná další neviděná úloha v tomhle filtru.")

    def _start_tactics_review(self) -> None:
        self.chk_tac_due.setChecked(True)
        if not self._tactics_all:
            self._find_tactics()          # po dokončení se filtr aplikuje sám
            return
        self._apply_tactics_filter()
        if self._tactics_shown:
            self.tac_table.selectRow(0)

    def _tactic_rate(self, useful: bool) -> None:
        p = self._tactics_cur
        if p is None:
            return
        self._tactics_store.set_useful(p.pid, useful, p)
        self._refresh_tactic_row(p)
        self.tac_feedback.setText(("Označeno jako užitečné." if useful
                                   else "Označeno jako neužitečné.")
                                  + f"  {self.tac_feedback.text()}")
        self._update_tactics_summary()

    def _update_tactics_summary(self) -> None:
        s = self._tactics_store.summary(p.pid for p in self._tactics_shown)
        if s["total"] == 0:
            if self.chk_tac_due.isChecked():
                self.tac_summary.setText(
                    "Na dnešek nic k opakování. Vyřeš nové úlohy (naplánují se na "
                    "příště podle toho, jak ti půjdou) nebo se vrať zítra.")
            else:
                self.tac_summary.setText(
                    "Žádné úlohy pro tenhle filtr. Buď rozeber víc partií na kartě "
                    "Přesnost, nebo uvolni filtr.")
            return
        self.tac_summary.setText(
            f"{s['total']} úloh · {s['unseen']} neviděno · {s['ok']} ✓ · {s['fail']} ✗ · "
            f"{s['seen']} rozkoukáno · ⏰ {s['due']} k opakování dnes · "
            f"👍 {s['useful_yes']} / 👎 {s['useful_no']} · ★ {s['starred']}")

    # ==================================================================== Report
    def _build_report_page(self) -> QWidget:
        page = QWidget()
        self._report_page = page
        lay = QVBoxLayout(page)

        row = QHBoxLayout()
        self.btn_report_save = QPushButton("💾 Uložit report aktuálního hráče")
        self.btn_report_save.clicked.connect(self._save_current_report)
        row.addWidget(self.btn_report_save)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel(
            "Report = snímek spočítaných statistik hráče. Ukládá výsledek rozboru "
            "z karty Přesnost (musí být spuštěný) a dopočítá statistiky z karty "
            "Vzorce. Vyber 2+ uložené reporty vlevo a klikni „Porovnat vybrané“.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        lay.addWidget(hint)

        split = QSplitter()

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.report_list = QListWidget()
        self.report_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.report_list.itemDoubleClicked.connect(lambda *_: self._compare_reports())
        ll.addWidget(self.report_list, stretch=1)
        self.btn_report_compare = QPushButton("Porovnat vybrané")
        self.btn_report_compare.clicked.connect(self._compare_reports)
        ll.addWidget(self.btn_report_compare)
        brow = QHBoxLayout()
        self.btn_report_rename = QPushButton("Přejmenovat")
        self.btn_report_rename.clicked.connect(self._rename_report)
        brow.addWidget(self.btn_report_rename)
        self.btn_report_delete = QPushButton("🗑 Smazat report")
        self.btn_report_delete.clicked.connect(self._delete_report)
        brow.addWidget(self.btn_report_delete)
        ll.addLayout(brow)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.report_summary = QLabel("Zatím není co porovnávat.")
        self.report_summary.setWordWrap(True)
        self.report_summary.setStyleSheet("color:#555;")
        rl.addWidget(self.report_summary)

        self.report_table = QTableWidget(0, 0)
        self.report_table.verticalHeader().setVisible(False)
        self.report_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.report_table.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self.report_table, stretch=1)

        crow = QHBoxLayout()
        crow.addWidget(QLabel("Graf:"))
        self.cmb_report_chart = QComboBox()
        for val, lbl in report_charts.COMPARE_CHARTS:
            self.cmb_report_chart.addItem(lbl, val)
        self.cmb_report_chart.currentIndexChanged.connect(lambda *_: self._draw_report_chart())
        crow.addWidget(self.cmb_report_chart)
        self.cmb_report_metric = QComboBox()
        for label, key in report_charts.TREND_METRICS:
            self.cmb_report_metric.addItem(label, key)
        self.cmb_report_metric.currentIndexChanged.connect(lambda *_: self._draw_report_chart())
        self.cmb_report_metric.setVisible(False)
        crow.addWidget(self.cmb_report_metric)
        crow.addStretch(1)
        self.btn_report_export = QPushButton("⬇ Export do PDF (všechny grafy)")
        self.btn_report_export.clicked.connect(self._export_report_pdf)
        crow.addWidget(self.btn_report_export)
        rl.addLayout(crow)

        self.report_chart_scroll = QScrollArea()
        self.report_chart_scroll.setWidgetResizable(True)
        self.report_chart_box = QWidget()
        self._report_chart_lay = QVBoxLayout(self.report_chart_box)
        self.report_chart_scroll.setWidget(self.report_chart_box)
        rl.addWidget(self.report_chart_scroll, stretch=1)

        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([320, 700])
        lay.addWidget(split, stretch=1)

        self._refresh_report_list()
        self._set_report_actions_enabled()
        return page

    def _set_report_actions_enabled(self) -> None:
        has_cmp = len(self._report_compare) >= 2
        self.btn_report_export.setEnabled(has_cmp)
        self.cmb_report_metric.setVisible(
            has_cmp and self.cmb_report_chart.currentData() == "trend")

    def _filter_desc(self) -> str:
        bits = []
        yr = years_range(self.games) if self.games else None
        if yr and self.spin_year_from.isEnabled():
            yf, yt = self.spin_year_from.value(), self.spin_year_to.value()
            if yf > yr[0] or yt < yr[1]:
                bits.append(f"{yf}–{yt}")
        if self.cmb_tc.currentIndex() > 0:
            bits.append(self.cmb_tc.currentText())
        if self.cmb_elo.currentIndex() > 0:
            bits.append(f"soupeř {self.cmb_elo.currentText()}")
        return ", ".join(bits) or "bez filtru"

    def _refresh_report_list(self) -> None:
        self.report_list.blockSignals(True)
        self.report_list.clear()
        for snap in self._report_store.all():
            marks = []
            if snap.get("accuracy"):
                marks.append("Přesnost")
            if snap.get("patterns"):
                marks.append("Vzorce")
            it = QListWidgetItem(f"{snap.get('label', snap['player'])}   [{' + '.join(marks) or '—'}]")
            it.setData(Qt.UserRole, snap["id"])
            self.report_list.addItem(it)
        self.report_list.blockSignals(False)

    def _selected_report_ids(self) -> list:
        return [it.data(Qt.UserRole) for it in self.report_list.selectedItems()]

    def _save_current_report(self) -> None:
        if self._report_builder is not None and self._report_builder.isRunning():
            return
        player = self.cmb_player.currentData()
        if not self.games or not player:
            QMessageBox.information(self, "Report", "Načti PGN databázi a vyber hráče.")
            return
        acc = self._acc_last_result
        if not acc:
            ans = QMessageBox.question(
                self, "Report",
                "Rozbor přesnosti (karta Přesnost) není spuštěný. Uložit report "
                "jen se statistikami z karty Vzorce?")
            if ans != QMessageBox.Yes:
                return
        self.btn_report_save.setEnabled(False)
        self.report_summary.setText(
            "Sestavuji report… (běží na pozadí – průchody přes celou databázi "
            "můžou u velkého PGN trvat i minutu, appka mezitím funguje dál)")
        self._report_builder = ReportBuilder(
            self.games, player, self._filter_keep, acc, self._filter_desc(), parent=self)
        self._report_builder.done.connect(self._on_report_built)
        self._report_builder.failed.connect(self._on_report_build_failed)
        self._report_builder.finished.connect(self._on_report_builder_finished)
        self._report_builder.start()

    def _on_report_built(self, snap: dict) -> None:
        self._report_store.save(snap)
        self._refresh_report_list()
        self.btn_report_save.setEnabled(True)
        self.report_summary.setText(
            f"Uložen report „{snap['label']}“. Vyber 2+ reporty (Ctrl+klik) a "
            f"klikni „Porovnat vybrané“.")

    def _on_report_build_failed(self, msg: str) -> None:
        self.btn_report_save.setEnabled(True)
        self.report_summary.setText(f"Report se nepodařilo sestavit: {msg}")

    def _on_report_builder_finished(self) -> None:
        self._report_builder = None

    def _rename_report(self) -> None:
        ids = self._selected_report_ids()
        if len(ids) != 1:
            QMessageBox.information(self, "Přejmenovat", "Vyber přesně jeden report.")
            return
        snap = self._report_store.get(ids[0])
        new, ok = QInputDialog.getText(self, "Přejmenovat report", "Název:",
                                       text=snap.get("label", ""))
        if ok and new.strip():
            self._report_store.rename(ids[0], new.strip())
            self._refresh_report_list()

    def _delete_report(self) -> None:
        ids = self._selected_report_ids()
        if not ids:
            QMessageBox.information(self, "Smazat report",
                                   "Vlevo v seznamu označ report(y), které chceš smazat.")
            return
        names = [self._report_store.get(i).get("label", i) for i in ids
                 if self._report_store.get(i)]
        if QMessageBox.question(
                self, "Smazat report",
                "Opravdu smazat?\n\n• " + "\n• ".join(names)) != QMessageBox.Yes:
            return
        for rid in ids:
            self._report_store.delete(rid)
        self._report_compare = [s for s in self._report_compare if s["id"] not in ids]
        self._refresh_report_list()
        if len(self._report_compare) >= 2:
            self._render_report_compare()
        else:
            self._report_compare = []
            self._clear_report_charts()
            self.report_table.setRowCount(0)
            self.report_table.setColumnCount(0)
            self.report_summary.setText("Report(y) smazány. Zatím není co porovnávat.")
        self._set_report_actions_enabled()

    def _compare_reports(self) -> None:
        ids = self._selected_report_ids()
        snaps = [self._report_store.get(i) for i in ids if self._report_store.get(i)]
        if len(snaps) < 2:
            QMessageBox.information(self, "Porovnat",
                                   "Vyber aspoň 2 uložené reporty (Ctrl+klik).")
            return
        snaps.sort(key=lambda s: s.get("saved_at", ""))
        self._report_compare = snaps
        self._render_report_compare()
        self._set_report_actions_enabled()

    def _render_report_compare(self) -> None:
        snaps = self._report_compare
        metrics = [extract_metrics(s) for s in snaps]
        rows = [(k, lbl, better, spec) for k, lbl, better, spec in COMPARE_ROWS
                if any(m.get(k) is not None for m in metrics)]

        t = self.report_table
        t.clear()
        t.setColumnCount(len(snaps))
        t.setRowCount(len(rows))
        t.setHorizontalHeaderLabels([s.get("label", s["player"]) for s in snaps])
        t.setVerticalHeaderLabels([lbl for _k, lbl, _b, _s in rows])
        t.verticalHeader().setVisible(True)
        for r, (key, _lbl, better, spec) in enumerate(rows):
            vals = [m.get(key) for m in metrics]
            best = best_index(key, better, vals)
            for c, v in enumerate(vals):
                txt = spec.format(v) if isinstance(v, (int, float)) else "–"
                it = QTableWidgetItem(txt)
                it.setTextAlignment(Qt.AlignCenter)
                if c in best:
                    it.setBackground(_score_brush(0.85))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                t.setItem(r, c, it)
        t.resizeColumnsToContents()

        parts = []
        stale = False
        for s in snaps:
            acc = s.get("accuracy") or {}
            d = f", hloubka {acc.get('depth')}" if acc.get("depth") else ""
            th = " (důkladný)" if acc.get("thorough") else ""
            parts.append(f"{s['player']}: {acc.get('n_games', 0)} partií{d}{th}"
                         f" · filtr {s.get('filter_desc', '?')}"
                         f" · {s.get('saved_at', '')[:10]}")
            if not s.get("distribution") or not acc.get("per_game"):
                stale = True
        txt = "   |   ".join(parts)
        if stale:
            txt += ("   ⚠ Některý report je starší verze bez dat pro všechny grafy "
                    "(rozdělení, deník, zahájení) – ulož ho znovu tlačítkem "
                    "„💾 Uložit report aktuálního hráče“.")
        self.report_summary.setText(txt)
        self._draw_report_chart()

    # ---------------------------------------------------------- srovnávací grafy
    def _clear_report_charts(self) -> None:
        lay = self._report_chart_lay
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _draw_report_chart(self) -> None:
        self._set_report_actions_enabled()
        self._clear_report_charts()
        if len(self._report_compare) < 2:
            return
        kind = self.cmb_report_chart.currentData()
        metric = self.cmb_report_metric.currentData()
        for _caption, obj in report_charts.build(kind, self._report_compare, metric):
            if isinstance(obj, QChart):
                v = QChartView(obj)
                v.setRenderHint(QPainter.Antialiasing)
                v.setMinimumHeight(340)
                self._report_chart_lay.addWidget(v)
            else:
                obj.setMinimumHeight(340)
                self._report_chart_lay.addWidget(obj)
        self._report_chart_lay.addStretch(1)

    @staticmethod
    def _rasterize(obj, w: int = 900, h: int = 520) -> QImage:
        if isinstance(obj, QChart):
            img = QImage(w, h, QImage.Format_ARGB32)
            img.fill(Qt.white)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            scene = QGraphicsScene()
            scene.addItem(obj)
            obj.setGeometry(QRectF(0, 0, w, h))
            scene.setSceneRect(0, 0, w, h)
            scene.render(p)
            scene.removeItem(obj)
            p.end()
            return img
        obj.setAttribute(Qt.WA_DontShowOnScreen, True)
        obj.resize(w, h)
        obj.show()
        img = obj.grab().toImage()
        obj.hide()
        return img

    _PDF_CHART_PLAN = (
        [("trend", m) for _lbl, m in report_charts.TREND_METRICS[:3]]
        + [(k, None) for k, _lbl in report_charts.COMPARE_CHARTS if k != "trend"])

    def _export_report_pdf(self) -> None:
        if len(self._report_compare) < 2:
            return
        default = "porovnani_" + "_".join(
            s["player"] for s in self._report_compare)[:60] + ".pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Uložit porovnání jako PDF", default, "PDF (*.pdf)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        images = []
        try:
            for kind, metric in self._PDF_CHART_PLAN:
                for caption, obj in report_charts.build(
                        kind, self._report_compare, metric or "acpl"):
                    tall = not isinstance(obj, QChart)
                    images.append((caption, self._rasterize(obj, 900, 620 if tall else 520)))
            from report_export import export_pdf
            export_pdf(path, self._report_compare, images)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Export selhal", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self.report_summary.setText(self.report_summary.text() + f"   → uloženo: {path}")

    def _update_chart(self) -> None:
        if not hasattr(self, "chart_view"):
            return
        kind = self.cmb_chart_kind.currentData()
        self.cmb_chart_metric.setVisible(kind == "trend")
        self.cmb_chart_color.setVisible(kind in self._COLOR_KINDS)
        builders = {
            "eval": self._chart_eval,
            "trend": self._chart_trend,
            "openings": self._chart_openings,
            "volatility": self._chart_volatility,
            "luck": self._chart_luck,
            "length": self._chart_length,
            "first_capture": self._chart_first_capture,
            "endgame_entry": self._chart_endgame_entry,
            "material": self._chart_material,
            "elo": self._chart_elo,
            "length_by_result": self._chart_length_by_result,
            "conversion": self._chart_conversion,
            "ep_wasted": self._chart_ep_wasted,
            "tactical": self._chart_tactical,
            "radar": self._chart_radar,
            "crit_scatter": self._chart_crit_scatter,
            "piece_acc": lambda: self._chart_piece(False),
            "piece_blund": lambda: self._chart_piece(True),
            "err_from": lambda: self._chart_error_map("f"),
            "err_to": lambda: self._chart_error_map("t"),
            "time_heatmap": self._chart_time_heatmap,
            "move_pie": self._chart_move_pie,
            "move_pie_db": self._chart_move_pie_db,
            "opponent_scatter": self._chart_opponent_scatter,
        }
        try:
            builders[kind]()
        except Exception as exc:  # ať vykreslení grafu appku nikdy nespadne
            self._set_chart_empty(f"Graf se nepodařilo vykreslit: {exc}")

    def _set_chart_empty(self, msg: str) -> None:
        chart = QChart()
        chart.setTitle(msg)
        chart.legend().hide()
        self.chart_view.setChart(chart)
        self.chart_stack.setCurrentWidget(self.chart_view)
        self.chart_note.setText(msg)

    def _show_histogram(self, values: list, bin_width: float, title: str, xlabel: str,
                        note: str) -> None:
        if not values:
            self._set_chart_empty(note or "Žádná data.")
            return
        self.chart_hist.set_data(values, bin_width, title, xlabel)
        self.chart_stack.setCurrentWidget(self.chart_hist)
        self.chart_note.setText(note)

    def _chart_eval(self) -> None:
        ev = self._game_eval
        if not ev or not self.game:
            self._set_chart_empty(
                "Nejdřív rozeber aktuální partii enginem: karta Partie → "
                "⚙ Rozebrat partii enginem.")
            return
        evals = ev["evals"]
        ws = win_series(evals, ev.get("wdls"))     # POV bílého, reálné W/D/L když je
        marks, mark_text, mark_fg, _ = self._active_marks()
        series = QLineSeries()
        series.setName("Šance na výhru (% bílý)")
        for ply, wv in enumerate(ws):
            series.append(ply, wv)
        chart = QChart()
        chart.addSeries(series)
        chart.setTitle(f"Eval graf – {self.game.label()}")
        for mk in mark_text:
            sc = QScatterSeries()
            sc.setName(mk)
            sc.setColor(QColor(mark_fg[mk]))
            sc.setMarkerSize(9)
            for ply, m in marks.items():
                if m == mk and 0 <= ply < len(ws):
                    sc.append(ply, ws[ply])
            if sc.count() > 0:
                chart.addSeries(sc)
        ax = QValueAxis()
        ax.setTitleText("půltah")
        ax.setLabelFormat("%d")
        ay = QValueAxis()
        ay.setRange(0, 100)
        ay.setTitleText("% (výhoda bílého)")
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        for s in chart.series():
            s.attachAxis(ax)
            s.attachAxis(ay)
        self.chart_view.setChart(chart)
        if self._active_style() == "chesscom":
            note = (f"{len(evals) - 1} tahů; styl chess.com – tyrkysová = brilantní, "
                   f"modrá = skvělý tah, fialová = přehlédnutí, červená = hrubka, "
                   f"oranžová = chyba, žlutá = nepřesnost.")
        else:
            note = f"{len(evals) - 1} tahů; červená = hrubka, oranžová = chyba, žlutá = nepřesnost."
        self.chart_note.setText(note)

    def _chart_trend(self) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        trend = next((rows for name, rows in res["sections"] if name == "Vývoj v čase"), None)
        if not trend:
            self._set_chart_empty(
                "Rozbor na kartě Přesnost nemá dost časového rozpětí na trend "
                "(potřeba rozebrané partie z aspoň 2 různých měsíců).")
            return
        metric = self.cmb_chart_metric.currentData()
        labels = {"acpl": "ACPL", "accuracy": "Přesnost %", "ipr": "IPR",
                 "conversion": "Konverze %", "resourcefulness": "Záchrana %",
                 "tact": "Tactical Awareness %"}
        series = QLineSeries()
        series.setName(labels[metric])
        cats = []
        for year, st in trend:
            val = st.get(metric)
            if val is None:
                continue
            series.append(len(cats), val)
            cats.append(year)
        if series.count() == 0:
            self._set_chart_empty(f"Pro „{labels[metric]}“ nejsou v trendu žádná data.")
            return
        chart = QChart()
        chart.addSeries(series)
        chart.legend().hide()
        chart.setTitle(f"Vývoj v čase – {labels[metric]}")
        ax = QBarCategoryAxis()
        ax.append(cats)
        ay = QValueAxis()
        ay.setTitleText(labels[metric])
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        series.attachAxis(ax)
        series.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_note.setText(
            f"Z rozboru na kartě Přesnost ({res['n_games']} partií, hloubka {res['depth']}).")

    def _chart_openings(self) -> None:
        player = self.cmb_player.currentData()
        if not self.games or not player:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        colors = self.cmb_chart_color.currentData()
        groups = analyze_openings(self.games, player, colors, keep=self._filter_keep)
        rows = []
        for g in groups:
            res = g.results()
            w, d, lo = res.count("win"), res.count("draw"), res.count("loss")
            dec = w + d + lo
            if dec:
                rows.append((g.label, len(res), w / dec))
        if not rows:
            self._set_chart_empty("Žádné partie k zobrazení.")
            return
        rows.sort(key=lambda r: -r[1])
        rows = rows[:15]
        rows.sort(key=lambda r: r[2])
        cats = [f"{lbl[:26]}  (n={n})" for lbl, n, _ in rows]
        barset = QBarSet("Winrate %")
        for _, _, wr in rows:
            barset.append(round(wr * 100, 1))
        series = QHorizontalBarSeries()
        series.append(barset)
        series.setLabelsVisible(True)
        series.setLabelsFormat("@value %")
        chart = QChart()
        chart.addSeries(series)
        chart.legend().hide()
        chart.setTitle(f"Winrate podle zahájení – top {len(rows)} podle počtu partií")
        ax = QBarCategoryAxis()
        ax.append(cats)
        ay = QValueAxis()
        ay.setRange(0, 100)
        ay.setTitleText("winrate %")
        chart.addAxis(ax, Qt.AlignLeft)
        chart.addAxis(ay, Qt.AlignBottom)
        series.attachAxis(ax)
        series.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_note.setText(
            "Přesný Wilsonův interval spolehlivosti pro každé zahájení viz "
            "tooltip na kartě Zahájení.")

    def _chart_volatility(self) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        vols = [r.get("vol") for r in res.get("per_game", []) if r.get("vol") is not None]
        if not vols:
            self._set_chart_empty("Rozbor na kartě Přesnost neobsahuje volatilitu.")
            return
        self._draw_histogram(
            vols, [0, 2, 4, 6, 8, 10, 12, float("inf")],
            ["0–2", "2–4", "4–6", "6–8", "8–10", "10–12", "12+"],
            "Divokost partií (průměrný skok šance na výhru na půltah, %)",
            f"{len(vols)} partií z rozboru na kartě Přesnost.")

    # ---------------------------------------------- obecný histogram + celá DB
    def _draw_histogram(self, values: list[float], edges: list[float], labels: list[str],
                        title: str, note: str, set_name: str = "Partií") -> None:
        counts = [0] * (len(edges) - 1)
        for v in values:
            for i in range(len(edges) - 1):
                if edges[i] <= v < edges[i + 1]:
                    counts[i] += 1
                    break
        barset = QBarSet(set_name)
        for c in counts:
            barset.append(c)
        series = QBarSeries()
        series.append(barset)
        chart = QChart()
        chart.addSeries(series)
        chart.legend().hide()
        chart.setTitle(title)
        ax = QBarCategoryAxis()
        ax.append(labels)
        ay = QValueAxis()
        ay.setTitleText("počet partií")
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        series.attachAxis(ax)
        series.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_stack.setCurrentWidget(self.chart_view)
        self.chart_note.setText(note)

    def _get_distribution(self, colors: str):
        """Vrátí (entries, player) – jedním průchodem přes CELOU (filtrovanou)
        databázi, cachované podle hráče/barvy/filtru, ať se při přepínání mezi
        histogramy nepočítá pořád dokola."""
        player = self.cmb_player.currentData()
        if not self.games or not player:
            return None, player
        key = (player, colors, id(self._filter_keep), len(self.games))
        if self._dist_cache is not None and self._dist_cache[0] == key:
            return self._dist_cache[1], player
        entries = distribution_stats(self.games, player, colors, keep=self._filter_keep)
        self._dist_cache = (key, entries)
        return entries, player

    def _chart_length(self) -> None:
        entries, player = self._get_distribution(self.cmb_chart_color.currentData())
        if entries is None:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        lengths = [e.moves for e in entries]
        self._show_histogram(
            lengths, 5, "Histogram délky partie", "délka partie (tahy)",
            f"{len(entries)} partií hráče {player}.")

    def _chart_first_capture(self) -> None:
        entries, player = self._get_distribution(self.cmb_chart_color.currentData())
        if entries is None:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        vals = [e.first_capture_move for e in entries if e.first_capture_move is not None]
        no_cap = len(entries) - len(vals)
        self._show_histogram(
            vals, 2, "Histogram prvního braní", "tah prvního braní",
            f"{len(entries)} partií hráče {player}; {no_cap} bez jediného braní "
            f"(do histogramu nejsou zahrnuty).")

    def _chart_endgame_entry(self) -> None:
        entries, player = self._get_distribution(self.cmb_chart_color.currentData())
        if entries is None:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        vals = [e.eg_entry_move for e in entries if e.eg_entry_move is not None]
        never = len(entries) - len(vals)
        self._show_histogram(
            vals, 5, "Histogram vstupu do koncovky", "tah vstupu do koncovky",
            f"{len(entries)} partií hráče {player}; {never} do koncovky nedošlo "
            f"(do histogramu nejsou zahrnuty).")

    def _chart_material(self) -> None:
        entries, player = self._get_distribution(self.cmb_chart_color.currentData())
        if entries is None:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        vals = [e.my_max_deficit for e in entries]
        self._show_histogram(
            vals, 1, "Histogram materiálového manka", "největší usazené manko (body)",
            f"{len(entries)} partií hráče {player}. „Usazené“ = úroveň, která přežila "
            f"i soupeřovu odpověď (odfiltruje běžné braní–zpětné braní); 0 = partie, "
            f"kde hráč nikdy nebyl pod materiálem.")

    def _chart_elo(self) -> None:
        entries, player = self._get_distribution(self.cmb_chart_color.currentData())
        if entries is None:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        vals = [e.elo_delta for e in entries if e.elo_delta is not None]
        no_elo = len(entries) - len(vals)
        self._show_histogram(
            vals, 50, "Histogram rozdílu Elo soupeře", "Elo hráč − soupeř",
            f"{len(entries)} partií hráče {player}; {no_elo} bez Ela soupeře "
            f"(do histogramu nejsou zahrnuty). Kladné = soupeř slabší.")

    def _chart_length_by_result(self) -> None:
        entries, player = self._get_distribution(self.cmb_chart_color.currentData())
        if entries is None:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        groups = {"win": [], "draw": [], "loss": []}
        for e in entries:
            if e.result in groups:
                groups[e.result].append(e.moves)
        all_vals = [v for vs in groups.values() for v in vs]
        if not all_vals:
            self._set_chart_empty("Žádné partie se známým výsledkem.")
            return
        bw = 15
        lo = (min(all_vals) // bw) * bw
        hi = ((max(all_vals) // bw) + 1) * bw
        n_bins = max(1, int((hi - lo) / bw))
        cats = [f"{lo + i * bw}" for i in range(n_bins)]
        chart = QChart()
        series = QBarSeries()
        names = {"win": "Výhra", "draw": "Remíza", "loss": "Prohra"}
        colors = {"win": QColor("#2e7d32"), "draw": QColor("#9e9e9e"), "loss": QColor("#c62828")}
        for key in ("win", "draw", "loss"):
            bset = QBarSet(names[key])
            bset.setColor(colors[key])
            counts = [0] * n_bins
            for v in groups[key]:
                idx = min(n_bins - 1, max(0, int((v - lo) / bw)))
                counts[idx] += 1
            for c in counts:
                bset.append(c)
            series.append(bset)
        chart.addSeries(series)
        chart.setTitle("Histogram délky partie podle výsledku")
        ax = QBarCategoryAxis()
        ax.append(cats)
        ay = QValueAxis()
        ay.setTitleText("počet partií")
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        series.attachAxis(ax)
        series.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_stack.setCurrentWidget(self.chart_view)
        self.chart_note.setText(
            f"{len(all_vals)} partií hráče {player} se známým výsledkem "
            f"(výhry {len(groups['win'])}, remízy {len(groups['draw'])}, "
            f"prohry {len(groups['loss'])}); koše po {bw} tazích, osa X = tah "
            f"na začátku koše.")

    def _chart_conversion(self) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        ov = res.get("overall") or {}
        conv, save = ov.get("conversion"), ov.get("resourcefulness")
        if conv is None and save is None:
            self._set_chart_empty("Rozbor nemá žádné jasně vyhrané ani prohrané pozice.")
            return
        cats = ["Vyhrané pozice → dotaženo", "Prohrané pozice → zachráněno"]
        barset = QBarSet("%")
        barset.append(conv if conv is not None else 0.0)
        barset.append(save if save is not None else 0.0)
        series = QBarSeries()
        series.append(barset)
        series.setLabelsVisible(True)
        series.setLabelsFormat("@value %")
        chart = QChart()
        chart.addSeries(series)
        chart.legend().hide()
        chart.setTitle("Dotahování: konverze vyhraných / záchrana prohraných pozic")
        ax = QBarCategoryAxis()
        ax.append(cats)
        ay = QValueAxis()
        ay.setRange(0, 100)
        ay.setTitleText("%")
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        series.attachAxis(ax)
        series.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_stack.setCurrentWidget(self.chart_view)
        self.chart_note.setText(
            f"Vyhrané pozice: {ov.get('had_win', 0)} partií, dotaženo "
            f"{conv if conv is not None else 0} %. Prohrané pozice: "
            f"{ov.get('had_loss', 0)} partií, zachráněno "
            f"{save if save is not None else 0} %. Z rozboru na kartě Přesnost.")

    def _chart_ep_wasted(self) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        vals = [r["ep_wasted"] for r in res.get("per_game", [])
                if r.get("had_win") and r.get("ep_wasted") is not None]
        if not vals:
            self._set_chart_empty("Rozbor nemá žádné jasně vyhrané pozice.")
            return
        self._show_histogram(
            vals, 0.05, "Histogram ztráty bodů z vyhraných pozic",
            "ztraceno z dosaženého maxima (body, 0–1)",
            f"{len(vals)} partií s jasně vyhranou pozicí, z rozboru na kartě Přesnost. "
            f"0 = pozice dotažena beze ztráty, blízko 1 = skoro vyhraná partie zahozena.")

    def _chart_tactical(self) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        if not res.get("thorough"):
            self._set_chart_empty(
                "Tactical Awareness potřebuje „důkladný rozbor“ (multipv) – zapni "
                "na kartě Přesnost a spusť znovu.")
            return
        vals = [r["tact_pct"] for r in res.get("per_game", []) if r.get("tact_pct") is not None]
        if not vals:
            self._set_chart_empty(
                "Žádná partie neměla dost kritických pozic (kritičnost ≥ "
                f"{TACT_CRIT_THRESHOLD:.2f}) pro Tactical Awareness.")
            return
        overall_tact = (res.get("overall") or {}).get("tact")
        self._show_histogram(
            vals, 5, "Histogram Tactical Awareness",
            "shoda s enginem v kritických pozicích (%)",
            f"{len(vals)} partií s aspoň jednou kritickou pozicí; celkově "
            f"{overall_tact} % (kritičnost ≥ {TACT_CRIT_THRESHOLD:.2f} – tam, kde "
            f"byl jasně nejlepší tah).")

    def _chart_radar(self) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        ov = res.get("overall") or {}
        acpl_score = None if ov.get("acpl") is None else max(0.0, 100.0 - ov["acpl"] / 3.0)
        axes_def = [
            ("Přesnost", ov.get("accuracy")),
            ("ACPL (invertovaně)", acpl_score),
            ("T1 %", ov.get("t1")),
            ("Tactical Aw.", ov.get("tact")),
            ("Konverze", ov.get("conversion")),
            ("Záchrana", ov.get("resourcefulness")),
        ]
        labels = [lbl for lbl, v in axes_def if v is not None]
        values = [v for _, v in axes_def if v is not None]
        n = len(values)
        if n < 3:
            self._set_chart_empty(
                "Málo naplněných os pro radar – zkus „důkladný rozbor“ (Tactical "
                "Awareness) a partie s jasně vyhranými/prohranými pozicemi "
                "(Konverze/Záchrana).")
            return
        chart = QPolarChart()
        chart.setTitle("Profil hráče")
        step = 360.0 / n
        series = QLineSeries()
        series.setName("Hráč")
        for i, v in enumerate(values):
            series.append(step * (i + 1), v)
        series.append(step, values[0])   # zavře smyčku zpět na první osu
        chart.addSeries(series)
        ax = QCategoryAxis()
        ax.setLabelsPosition(QCategoryAxis.AxisLabelsPositionOnValue)
        for i, lbl in enumerate(labels):
            ax.append(lbl, step * (i + 1))
        ax.setRange(0, 360)
        ay = QValueAxis()
        ay.setRange(0, 100)
        ay.setLabelFormat("%d")
        chart.addAxis(ax, QPolarChart.PolarOrientationAngular)
        chart.addAxis(ay, QPolarChart.PolarOrientationRadial)
        series.attachAxis(ax)
        series.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_stack.setCurrentWidget(self.chart_view)
        self.chart_note.setText(
            "Normalizováno na 0–100 (ACPL invertovaně: 100 − ACPL/3, ať menší "
            "ACPL = dál od středu jako ostatní osy). Z rozboru na kartě Přesnost.")

    def _chart_crit_scatter(self) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        ca = res.get("crit_accuracy")
        if not ca:
            self._set_chart_empty(
                "Potřebuje „důkladný rozbor“ (multipv) – zapni na kartě Přesnost "
                "a spusť znovu.")
            return
        edges, means, ns = ca["edges"], ca["mean_acc"], ca["n"]
        series = QLineSeries()
        series.setName("Průměrná přesnost tahu")
        scatter = QScatterSeries()
        scatter.setMarkerSize(9)
        scatter.setName("Koše kritičnosti")
        for i, m in enumerate(means):
            if m is None:
                continue
            mid = (edges[i] + edges[i + 1]) / 2
            series.append(mid, m)
            scatter.append(mid, m)
        if series.count() < 2:
            self._set_chart_empty(
                "Málo dat pro tenhle graf (potřeba víc partií s „důkladným rozborem“).")
            return
        chart = QChart()
        chart.addSeries(series)
        chart.addSeries(scatter)
        chart.setTitle("Přesnost tahu podle kritičnosti pozice")
        ax = QValueAxis()
        ax.setRange(0, 1)
        ax.setTitleText("kritičnost pozice (0 = klid, 1 = jasně nejlepší tah)")
        ay = QValueAxis()
        ay.setRange(0, 100)
        ay.setTitleText("průměrná přesnost tahu (%)")
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        for s in (series, scatter):
            s.attachAxis(ax)
            s.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_stack.setCurrentWidget(self.chart_view)
        self.chart_note.setText(
            f"{sum(ns)} tahů z „důkladného rozboru“ rozdělených do {len(ns)} košů "
            f"kritičnosti (šířka {1 / len(ns):.2f}); klesající křivka = víc chyb "
            f"v ostřejších pozicích.")

    _PIECE_ORDER = [(chess.PAWN, "pěšec"), (chess.KNIGHT, "jezdec"),
                    (chess.BISHOP, "střelec"), (chess.ROOK, "věž"),
                    (chess.QUEEN, "dáma"), (chess.KING, "král")]
    _MTYPE_ORDER = ["braní", "tichý tah", "šach", "rošáda", "proměna"]

    def _chart_piece(self, blunders: bool) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        pa = res.get("piece_accuracy") or {}
        ma = res.get("movetype_accuracy") or {}
        key = "blund_100" if blunders else "acc"
        cats: list[str] = []
        vals: list[float] = []
        ns: list[int] = []
        for pt, label in self._PIECE_ORDER:
            d = pa.get(pt) or pa.get(str(pt))
            if d and d.get("n") and d.get(key) is not None:
                cats.append(label)
                vals.append(d[key])
                ns.append(d["n"])
        for mt in self._MTYPE_ORDER:
            d = ma.get(mt)
            if d and d.get("n") and d.get(key) is not None:
                cats.append(mt)
                vals.append(d[key])
                ns.append(d["n"])
        if not cats:
            self._set_chart_empty("Rozbor na kartě Přesnost zatím nemá tahy k rozdělení "
                                  "podle figury.")
            return
        title = ("Hrubky podle tažené figury a typu tahu"
                 if blunders else "Přesnost tahu podle tažené figury a typu tahu")
        bs = QBarSet("hrubky / 100 tahů" if blunders else "přesnost %")
        for v in vals:
            bs.append(float(v))
        bs.setColor(QColor("#c62828" if blunders else "#2e7d32"))
        series = QBarSeries()
        series.append(bs)
        series.setLabelsVisible(True)
        series.setLabelsFormat("@value")
        chart = QChart()
        chart.addSeries(series)
        chart.legend().hide()
        chart.setTitle(title)
        ax = QBarCategoryAxis()
        ax.append(cats)
        ay = QValueAxis()
        if blunders:
            ay.setRange(0.0, max(vals) * 1.2 or 1.0)
            ay.setTitleText("hrubky na 100 tahů hráče")
        else:
            lo = max(0.0, min(vals) - 6.0)
            ay.setRange(lo, 100.0)
            ay.setTitleText("průměrná přesnost tahu (%)")
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        series.attachAxis(ax)
        series.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_stack.setCurrentWidget(self.chart_view)
        self.chart_note.setText(
            "Jen tahy hráče, z posledního rozboru na kartě Přesnost. Levá část = "
            "podle tažené figury, pravá = podle typu tahu (tytéž tahy, jiný pohled). "
            "Počty tahů: " + ", ".join(f"{c} {n}" for c, n in zip(cats, ns)) + ".")

    def _chart_error_map(self, which: str) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        em = res.get("error_map") or {}
        bad = em.get("mf" if which == "f" else "mt") or [0] * 64
        blu = em.get("bf" if which == "f" else "bt") or [0] * 64
        allm = em.get("af" if which == "f" else "at") or [0] * 64
        if not any(bad):
            self._set_chart_empty("Rozbor na kartě Přesnost zatím nemá chyby k zobrazení.")
            return
        self.chart_errmap.set_data(bad, chess.WHITE)
        self.chart_stack.setCurrentWidget(self.chart_errmap)
        tot_bad, tot_bl = sum(bad), sum(blu)
        odkud = "odkud táhla figura" if which == "f" else "kam figura táhla"
        # nejhorší pole podle podílu chyb
        worst = sorted(
            ((i, bad[i], allm[i]) for i in range(64) if allm[i] >= 5),
            key=lambda x: -(x[1] / x[2]))[:3]
        wtxt = "; ".join(f"{chess.square_name(i)} {b}/{a} ({100 * b / a:.0f} %)"
                         for i, b, a in worst)
        self.chart_note.setText(
            f"Počet chyb (nepřesnost + chyba + hrubka, z toho {tot_bl} hrubek) podle "
            f"pole, {odkud} – sjednoceno na perspektivu hráče (tvá 1. řada dole). "
            f"Celkem {tot_bad} chybných tahů z posledního rozboru na kartě Přesnost. "
            + (f"Nejchybovější pole (chyby / všechny tahy): {wtxt}." if wtxt else ""))

    def _chart_time_heatmap(self) -> None:
        player = self.cmb_player.currentData()
        if not self.games or not player:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        colors = self.cmb_chart_color.currentData()
        log = game_log(self.games, player, colors, keep=self._filter_keep)
        wins = [[0] * 24 for _ in range(7)]
        totals = [[0] * 24 for _ in range(7)]
        n_no_time = 0
        for dt, _delta, result in log:
            if dt is None or result not in ("win", "draw", "loss"):
                n_no_time += 1
                continue
            loc = to_prague_local(dt)
            d, h = loc.weekday(), loc.hour
            totals[d][h] += 1
            if result == "win":
                wins[d][h] += 1
        total_known = sum(sum(row) for row in totals)
        if total_known == 0:
            self._set_chart_empty(
                "Žádné partie se známým časem (UTCDate/UTCTime nebo Date+Time "
                "v hlavičce PGN).")
            return
        winrate = [[(wins[d][h] / totals[d][h] if totals[d][h] else None) for h in range(24)]
                  for d in range(7)]
        self.chart_heat.set_data(winrate, totals)
        self.chart_stack.setCurrentWidget(self.chart_heat)
        extra = f"; {n_no_time} bez času (nezahrnuty)" if n_no_time else ""
        self.chart_note.setText(
            f"{total_known} partií hráče {player} se známým časem{extra}. Winrate = "
            f"podíl výher (remízy se počítají do jmenovatele, ne do čitatele – stejná "
            f"definice jako u grafu „Winrate podle zahájení“). Čas je z UTCTime v PGN "
            f"hlavičce převedený na místní čas ČR (CET/CEST, včetně letního času).")

    def _build_move_pie(self, counts: dict, title: str,
                        pct_slices: bool = False) -> tuple[int, bool]:
        """Sestaví koláč z {kind: count} do self.chart_view. Vrátí (total, has_great)
        – ať volající může doplnit poznámku (celkem tahů, chybí-li Skvělé tahy).

        ``pct_slices=True``: na výsečích jsou procenta, v legendě počty tahů
        (jinak počty na výsečích i v legendě)."""
        total = sum(counts.values())
        if not total:
            return 0, False
        series = QPieSeries()
        used: list = []
        for kind in KIND_ORDER:
            c = counts.get(kind, 0)
            if not c:
                continue
            pct = 100.0 * c / total
            lbl = f"{pct:.0f} %" if pct_slices else f"{KIND_LABEL[kind]} ({c})"
            slc = series.append(lbl, c)
            slc.setBrush(QColor(KIND_COLOR[kind]))
            slc.setLabelVisible(pct >= 3.0)
            used.append((kind, c))
        chart = QChart()
        chart.addSeries(series)
        chart.setTitle(title)
        if pct_slices:
            for marker, (kind, c) in zip(chart.legend().markers(series), used):
                marker.setLabel(f"{KIND_LABEL[kind]} ({c})")
        self.chart_view.setChart(chart)
        self.chart_stack.setCurrentWidget(self.chart_view)
        return total, bool(counts.get("great"))

    def _chart_move_pie(self) -> None:
        ev = self._game_eval
        if not ev or not self.game:
            self._set_chart_empty(
                "Nejdřív rozeber aktuální partii enginem: karta Partie → "
                "⚙ Rozebrat partii enginem.")
            return
        counts = ev.get("cc_counts") or {}
        total, has_great = self._build_move_pie(
            counts, f"Tahy podle kategorie (chess.com) – {self.game.label()}")
        if not total:
            self._set_chart_empty("Rozbor nemá žádné tahy k zobrazení.")
            return
        self.chart_note.setText(
            f"{total} tahů (obě strany dohromady) z aktuálně rozebrané partie na "
            f"kartě Partie – nezávisí na vybraném stylu (?/! vs. Best/…), počítá se "
            f"vždy multipv=3, viz karta Partie.")

    def _chart_move_pie_db(self) -> None:
        res = self._acc_last_result
        if not res:
            self._set_chart_empty("Nejdřív spusť rozbor na kartě Přesnost.")
            return
        counts = res.get("cc_counts") or {}
        total, has_great = self._build_move_pie(
            counts, f"Tahy podle kategorie (chess.com) – {res['n_games']} partií",
            pct_slices=True)
        if not total:
            self._set_chart_empty("Rozbor na kartě Přesnost nemá žádné tahy k zobrazení.")
            return
        note = (f"{total} tahů z {res['n_games']} partií (obě strany dohromady) "
               f"z rozboru na kartě Přesnost. V legendě je počet tahů v kategorii, "
               f"na výsečích jejich podíl v procentech.")
        if not has_great and not res.get("thorough"):
            note += (" Skvělé tahy se bez „důkladného rozboru“ (multipv) nikdy "
                    "nepoznají – zapni ho a spusť rozbor znovu, pokud je chceš vidět.")
        self.chart_note.setText(note)

    def _chart_opponent_scatter(self) -> None:
        player = self.cmb_player.currentData()
        if not self.games or not player:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        colors = self.cmb_chart_color.currentData()
        rows = rating_pairs(self.games, player, colors, keep=self._filter_keep)
        if not rows:
            self._set_chart_empty("Žádné partie se známým Elem obou stran.")
            return
        n = len(rows)
        alpha = max(60, min(230, round(6000 / max(1, n) ** 0.5)))
        lo = min(min(m, o) for m, o, _ in rows)
        hi = max(max(m, o) for m, o, _ in rows)

        series_by_result = {"win": QScatterSeries(), "draw": QScatterSeries(),
                            "loss": QScatterSeries()}
        names = {"win": "Výhra", "draw": "Remíza", "loss": "Prohra"}
        colors_map = {"win": "#2e7d32", "draw": "#9e9e9e", "loss": "#c62828"}
        for key, s in series_by_result.items():
            s.setName(names[key])
            col = QColor(colors_map[key])
            col.setAlpha(alpha)
            s.setColor(col)
            s.setBorderColor(Qt.transparent)
            s.setMarkerSize(7)
        for mine, opp, result in rows:
            if result in series_by_result:
                series_by_result[result].append(opp, mine)

        diag = QLineSeries()
        diag.setName("stejné Elo")
        diag.append(lo, lo)
        diag.append(hi, hi)
        pen = QPen(QColor("#999999"))
        pen.setStyle(Qt.DashLine)
        diag.setPen(pen)

        chart = QChart()
        chart.setTitle(f"Elo hráče × Elo soupeře – {player}")
        chart.addSeries(diag)
        for s in series_by_result.values():
            if s.count() > 0:
                chart.addSeries(s)
        ax = QValueAxis()
        ax.setTitleText("Elo soupeře")
        ay = QValueAxis()
        ay.setTitleText(f"Elo hráče ({player})")
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        for s in chart.series():
            s.attachAxis(ax)
            s.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_stack.setCurrentWidget(self.chart_view)
        self.chart_note.setText(
            f"{n} partií hráče {player} se známým Elem obou stran. Nad přerušovanou "
            f"čarou „stejné Elo“ = hráč měl víc Ela (favorit); pod ní = hrál proti "
            f"silnějšímu soupeři. Poloprůhledné tečky – překryv ukazuje hustotu.")

    def _chart_luck(self) -> None:
        player = self.cmb_player.currentData()
        if not self.games or not player:
            self._set_chart_empty("Načti PGN databázi a vyber hráče.")
            return
        colors = self.cmb_chart_color.currentData()
        log = game_log(self.games, player, colors, keep=self._filter_keep)
        score_map = {"win": 1.0, "draw": 0.5, "loss": 0.0}
        series = QLineSeries()
        series.setName("Kumulativní (skutečné − Elo-očekávané)")
        cum = 0.0
        n = 0
        for _dt, delta, result in log:
            if delta is None or result not in score_map:
                continue
            n += 1
            cum += score_map[result] - elo_expected_from_delta(delta)
            series.append(n, cum)
        if series.count() < 2:
            self._set_chart_empty("Málo partií se známým Elem soupeře pro tenhle graf.")
            return
        chart = QChart()
        chart.addSeries(series)
        chart.legend().hide()
        chart.setTitle("Kumulativní „štěstí“ (skutečné skóre − Elo-očekávané, chronologicky)")
        ax = QValueAxis()
        ax.setTitleText("partie (chronologicky, jen se známým Elem soupeře)")
        ax.setLabelFormat("%d")
        ay = QValueAxis()
        ay.setTitleText("kumulativní body")
        chart.addAxis(ax, Qt.AlignBottom)
        chart.addAxis(ay, Qt.AlignLeft)
        series.attachAxis(ax)
        series.attachAxis(ay)
        self.chart_view.setChart(chart)
        self.chart_note.setText(
            f"{n} partií se známým Elem soupeře. Stoupá = série nad očekávání, "
            f"klesá = pod (viz Elo-adjusted výkonnost na kartě Vzorce).")

    def _build_menu(self) -> None:
        m_file = self.menuBar().addMenu("&Soubor")
        act_open = QAction("&Otevřít PGN…", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self.open_pgn)
        m_file.addAction(act_open)

        act_paste = QAction("Vložit PGN z textu…", self)
        act_paste.triggered.connect(self.paste_pgn)
        m_file.addAction(act_paste)

        m_file.addSeparator()
        act_quit = QAction("Konec", self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addAction(act_quit)

        m_engine = self.menuBar().addMenu("&Engine")
        act_path = QAction("Nastavit cestu k enginu (Stockfish)…", self)
        act_path.triggered.connect(self.set_engine_path)
        m_engine.addAction(act_path)

        act_perf = QAction("Nastavit výkon enginu (vlákna, paměť)…", self)
        act_perf.triggered.connect(self.set_engine_perf)
        m_engine.addAction(act_perf)

        self.act_use_wdl = QAction("Používat reálné W/D/L z enginu", self)
        self.act_use_wdl.setCheckable(True)
        self.act_use_wdl.setChecked(bool(self.config.get("use_wdl", True)))
        self.act_use_wdl.setToolTip(
            "Šanci na výhru a očekávané body počítat z reálného W/D/L enginu "
            "(zná contempt, pravidlo 50 tahů, typ pozice) místo ze sigmoidy z "
            "centipawnů. Přesnější pro praktickou hru; čísla přesnosti tím ale "
            "nejsou srovnatelná s lichess a starší rozbory se přepočítají až "
            "při dalším spuštění.")
        self.act_use_wdl.toggled.connect(self._on_toggle_wdl)
        m_engine.addAction(self.act_use_wdl)

        m_engine.addSeparator()
        act_clear_cache = QAction("Smazat cache rozborů partií…", self)
        act_clear_cache.setToolTip(
            "Smaže analysis_cache.json.gz – uložené výsledky rozborů partií enginem "
            "(karty Přesnost, Partie, Taktika). Po smazání se musí spočítat znovu.")
        act_clear_cache.triggered.connect(self._clear_analysis_cache)
        m_engine.addAction(act_clear_cache)
        act_clear_tac = QAction("Smazat postup v taktických úlohách…", self)
        act_clear_tac.setToolTip(
            "Smaže tactics_progress.json – co jsi u úloh viděl/vyřešil, hodnocení "
            "užitečnosti, hvězdičky a plán opakování. Úlohy samotné zůstanou "
            "(odvodí se z rozboru), smaže se jen tvůj postup.")
        act_clear_tac.triggered.connect(self._clear_tactics_progress)
        m_engine.addAction(act_clear_tac)

        m_nav = self.menuBar().addMenu("&Navigace")
        for text, seq, fn in (
            ("Další tah", Qt.Key_Right, lambda: self.set_ply(self.ply + 1)),
            ("Předchozí tah", Qt.Key_Left, lambda: self.set_ply(self.ply - 1)),
            ("Na začátek", Qt.Key_Home, lambda: self.set_ply(0)),
            ("Na konec", Qt.Key_End, lambda: self.set_ply(self.game.ply_count if self.game else 0)),
            ("Otočit šachovnici", Qt.Key_F, self._flip_boards),
            ("Přehrát / pauza", Qt.Key_Space, self._toggle_autoplay),
        ):
            a = QAction(text, self)
            a.setShortcut(QKeySequence(seq))
            a.triggered.connect(fn)
            m_nav.addAction(a)

    # ----------------------------------------------------------- načítání
    def _load_startposition(self) -> None:
        self.games = []
        self.game = LoadedGame({}, [])
        self.ply = 0
        self._refresh_game_selector()
        self._refresh_player_combo()
        self._refresh_filter_years()
        self._filter_keep = None
        self._rebuild_move_table()
        self._render()
        self._update_opening_tree()
        self._invalidate_analyses()

    def open_pgn(self) -> None:
        start_dir = self.config.get("last_dir", "")
        path, _ = QFileDialog.getOpenFileName(self, "Otevřít PGN", start_dir, "PGN soubory (*.pgn);;Vše (*.*)")
        if not path:
            return
        self.config["last_dir"] = os.path.dirname(path)
        save_config(self.config)
        self._start_loading(path=path, source=os.path.basename(path))

    def paste_pgn(self) -> None:
        text, ok = QInputDialog.getMultiLineText(self, "Vložit PGN", "Vlož text PGN:")
        if not ok or not text.strip():
            return
        self._start_loading(text=text, source="vložený text")

    def _start_loading(self, *, path: str | None = None, text: str | None = None,
                       source: str) -> None:
        if getattr(self, "_loader", None) is not None and self._loader.isRunning():
            return
        self._loader = PgnLoader(path=path, text=text)
        self._loader.progress.connect(
            lambda n: self.statusBar().showMessage(f"Načítám PGN… {n} partií"))
        self._loader.failed.connect(
            lambda msg: QMessageBox.critical(self, "Chyba", f"PGN se nepodařilo načíst:\n{msg}"))
        self._loader.loaded.connect(lambda games: self._set_games(games, source))
        self.statusBar().showMessage(f"Načítám PGN ({source})…")
        self._loader.start()

    def _set_games(self, games: list[LoadedGame], source: str) -> None:
        if not games:
            QMessageBox.warning(self, "Nic k načtení", "V PGN nebyla nalezena žádná partie.")
            return
        self.games = games
        self._filter_keep = None
        self.statusBar().showMessage(f"Načteno {len(games)} partií z: {source}", 5000)
        self._refresh_game_selector()
        self._refresh_player_combo()
        self._refresh_filter_years()
        self.game_selector.setCurrentIndex(0)
        self._on_game_selected(0)
        self._invalidate_analyses()
        self.statusBar().showMessage(
            f"Načteno {len(games)} partií – rozbory na kartách spustíš tlačítkem „Spustit rozbor“.",
            8000)

    def _refresh_game_selector(self) -> None:
        self.game_selector.blockSignals(True)
        self.game_selector.clear()
        if self.games:
            for i, g in enumerate(self.games, 1):
                self.game_selector.addItem(f"{i}. {g.label()}")
            self.game_selector.setEnabled(len(self.games) > 1)
        else:
            self.game_selector.addItem("Základní pozice")
            self.game_selector.setEnabled(False)
        self.game_selector.blockSignals(False)

    def _on_game_selected(self, index: int) -> None:
        if not self.games or not (0 <= index < len(self.games)):
            return
        self.game = self.games[index]
        self.ply = 0
        self._cancel_game_analysis()
        self._game_eval = None
        self.ga_summary.setText("")
        self.btn_game_analyze.setText("⚙ Rozebrat partii enginem")
        self.ga_legend.hide()
        if hasattr(self, "crit_row"):
            self.crit_row.hide()
            self._crit_plies = []
        self._update_header()
        self._rebuild_move_table()
        prev_player = self.cmb_player.currentData()
        self._sync_player_to_game()
        self.set_ply(0)
        if self.cmb_player.currentData() != prev_player:
            self._invalidate_analyses()

    # --------------------------------------------------------- navigace
    @property
    def in_analysis(self) -> bool:
        return bool(self.analysis_moves)

    def current_board(self) -> chess.Board:
        base = self.game.board_at(self.ply) if self.game else chess.Board()
        board = base.copy()
        for mv in self.analysis_moves:
            board.push(mv)
        return board

    def current_lastmove(self) -> chess.Move | None:
        if self.analysis_moves:
            return self.analysis_moves[-1]
        return self.game.lastmove_at(self.ply) if self.game else None

    def set_ply(self, ply: int) -> None:
        if self.game is None:
            return
        self.analysis_moves = []
        ply = max(0, min(ply, self.game.ply_count))
        self.ply = ply
        self._after_position_change()
        self._update_comment()
        if self.autoplay.isActive() and ply >= self.game.ply_count:
            self._toggle_autoplay()

    def _after_position_change(self) -> None:
        """Společné kroky po jakékoli změně zobrazené pozice."""
        self._render()
        self._highlight_current_move()
        self._update_analysis_label()
        self.board.set_arrows([])
        self._clear_variation()
        self._update_opening_tree()
        self._update_eval_bar()
        if self.engine is not None:
            self.engine.set_position(self.current_board())

    def _update_eval_bar(self) -> None:
        """Ukazatel hodnocení – z rozboru partie (když je) a bez enginu."""
        if self.engine is not None:
            return  # živý engine si bar řídí sám v _on_engine_info
        ev = self._game_eval
        if ev and not self.analysis_moves and 0 <= self.ply < len(ev["evals"]):
            self.eval_bar.set_eval(cp=ev["evals"][self.ply])
        else:
            self.eval_bar.clear()

    def _render(self) -> None:
        self.board.set_position(self.current_board(), self.current_lastmove())
        total = self.game.ply_count if self.game else 0
        suffix = f"  +{len(self.analysis_moves)} (analýza)" if self.analysis_moves else ""
        self.setWindowTitle(f"Šachy – tah {self.ply}/{total}{suffix}")

    # ----------------------------------------------------- vlastní tahy
    def _on_user_move(self, move: chess.Move) -> None:
        if self.autoplay.isActive():
            self._toggle_autoplay()
        self.analysis_moves.append(move)
        self._after_position_change()

    def _go_prev(self) -> None:
        if self.analysis_moves:
            self.analysis_moves.pop()
            self._after_position_change()
        else:
            self.set_ply(self.ply - 1)

    def _go_next(self) -> None:
        if not self.analysis_moves:
            self.set_ply(self.ply + 1)

    def _exit_analysis(self) -> None:
        self.analysis_moves = []
        self._after_position_change()

    def _update_analysis_label(self) -> None:
        active = bool(self.analysis_moves)
        self.btn_exit_analysis.setVisible(active)
        if not active:
            self.analysis_label.setText("")
            return
        base = self.game.board_at(self.ply).copy()
        parts: list[str] = []
        for i, mv in enumerate(self.analysis_moves):
            if base.turn == chess.WHITE:
                parts.append(f"{base.fullmove_number}.")
            elif i == 0:
                parts.append(f"{base.fullmove_number}...")
            parts.append(base.san(mv))
            base.push(mv)
        self.analysis_label.setText("Analýza: " + " ".join(parts))

    def _on_arrows_toggled(self, on: bool) -> None:
        if not on:
            self.board.set_arrows([])

    def _toggle_autoplay(self) -> None:
        if self.autoplay.isActive():
            self.autoplay.stop()
            self.btn_play.setText("▶ Přehrát")
        elif self.game and not self.analysis_moves and self.ply < self.game.ply_count:
            self.autoplay.start()
            self.btn_play.setText("⏸ Pauza")

    def _autoplay_step(self) -> None:
        if self.game and self.ply < self.game.ply_count:
            self.set_ply(self.ply + 1)
        else:
            self._toggle_autoplay()

    # ------------------------------------------------------- seznam tahů
    def _rebuild_move_table(self) -> None:
        self.move_table.setRowCount(0)
        self._cell_for_ply: dict[int, tuple[int, int]] = {}
        self._mark_for_ply: dict[int, str] = {}
        if not self.game or self.game.ply_count == 0:
            return
        marks, mark_text, mark_fg, mark_bg = self._active_marks()
        for ply in range(1, self.game.ply_count + 1):
            board_before = self.game.boards[ply - 1]
            fullmove = board_before.fullmove_number
            is_white = board_before.turn == chess.WHITE
            col = 1 if is_white else 2
            row = self._row_for_fullmove(fullmove)
            san = self.game.moves_san[ply - 1]
            mk = marks.get(ply)
            item = QTableWidgetItem(f"{san} {mk}" if mk else san)
            item.setData(PLY_ROLE, ply)
            if mk:
                self._mark_for_ply[ply] = mk
                item.setBackground(QColor(mark_bg[mk]))
                item.setForeground(QColor(mark_fg[mk]))
                fnt = item.font()
                fnt.setBold(True)
                item.setFont(fnt)
                item.setToolTip(self._mark_tooltip(mk, ply, mark_text))
            self.move_table.setItem(row, col, item)
            self._cell_for_ply[ply] = (row, col)
        self.move_table.resizeColumnsToContents()
        self.move_table.horizontalHeader().setStretchLastSection(True)

    @staticmethod
    def _wdl_pov(wdl, white_pov: bool):
        """[w,d,l] bílého → z pohledu strany na tahu (pro accuracy.win_prob)."""
        if not wdl:
            return None
        return list(wdl) if white_pov else [wdl[2], wdl[1], wdl[0]]

    def _mark_tooltip(self, mk: str, ply: int, mark_text: dict | None = None) -> str:
        mark_text = mark_text if mark_text is not None else MARK_TEXT
        lbl = mark_text.get(mk, mk)
        if self._active_style() == "chesscom" and mk in ("!!", "!", "✗"):
            extra = {
                "!!": "obětoval materiál, pozice zůstala v pořádku (nebyla beztak vyhraná)",
                "!": "kritická pozice – tohle byla jasně nejlepší (často jediná dobrá) volba",
                "✗": "soupeř před tím udělal chybu a tenhle tah ji nevyužil",
            }[mk]
            return f"{mk} {lbl} – {extra}"
        ev = self._game_eval or {}
        evals = ev.get("evals") or []
        if 0 < ply < len(evals):
            mover_white = (ply - 1) % 2 == 0
            b, a = evals[ply - 1], evals[ply]
            wl = ev.get("wdls") or []
            w0 = win_prob(b if mover_white else -b,
                          self._wdl_pov(wl[ply - 1] if ply - 1 < len(wl) else None, mover_white))
            w1 = win_prob(a if mover_white else -a,
                          self._wdl_pov(wl[ply] if ply < len(wl) else None, mover_white))
            drop = max(0.0, w0 - w1)
            cp_loss = max(0, (b - a) if mover_white else (a - b))
            tail = "ztráta rozhodující výhody" if cp_loss >= 9000 else \
                f"ztráta ≈ {cp_loss / 100:.1f} pěšce"
            return f"{mk} {lbl} – šance na výhru klesla o {drop:.0f} % ({tail})"
        return f"{mk} {lbl}"

    def _row_for_fullmove(self, fullmove: int) -> int:
        for row in range(self.move_table.rowCount()):
            it = self.move_table.item(row, 0)
            if it and int(it.text()) == fullmove:
                return row
        row = self.move_table.rowCount()
        self.move_table.insertRow(row)
        num = QTableWidgetItem(str(fullmove))
        num.setFlags(Qt.ItemIsEnabled)
        self.move_table.setItem(row, 0, num)
        return row

    def _on_move_cell_clicked(self, row: int, col: int) -> None:
        item = self.move_table.item(row, col)
        if item is None:
            return
        ply = item.data(PLY_ROLE)
        if ply is not None:
            self.set_ply(int(ply))

    def _highlight_current_move(self) -> None:
        current = -1 if self.analysis_moves else self.ply
        marks = getattr(self, "_mark_for_ply", {})
        _, _, _, mark_bg = self._active_marks()
        for ply, (row, col) in getattr(self, "_cell_for_ply", {}).items():
            item = self.move_table.item(row, col)
            if item is None:
                continue
            if ply == current:
                item.setBackground(Qt.yellow)
                self.move_table.scrollToItem(item)
            elif ply in marks:
                item.setBackground(QColor(mark_bg[marks[ply]]))
            else:
                item.setBackground(Qt.transparent)

    def _update_comment(self) -> None:
        self.comment_label.setText(self.game.comment_at(self.ply) if self.game else "")

    def _update_header(self) -> None:
        if self.game and self.game.headers:
            self.header_label.setText(self.game.label())
        else:
            self.header_label.setText("Základní pozice")

    # ------------------------------------------------------------ engine
    def set_engine_path(self) -> None:
        start_dir = os.path.dirname(self.config.get("engine_path", "")) or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Vyber spustitelný soubor enginu (UCI)", start_dir,
            "Spustitelné (*.exe);;Vše (*.*)")
        if not path:
            return
        self.config["engine_path"] = path
        save_config(self.config)
        self._update_engine_status()
        if self.chk_engine.isChecked():
            self._restart_engine()

    def set_engine_perf(self) -> None:
        cpu = os.cpu_count() or 4
        dlg = QDialog(self)
        dlg.setWindowTitle("Výkon enginu")
        form = QFormLayout(dlg)
        info = QLabel(
            f"Tvůj počítač hlásí {cpu} vláken CPU.\n\n"
            f"• Vlákna (uvnitř 1 enginu): naměřeno, že u tohohle způsobu volání "
            f"(engine zvlášť na každou pozici) víc vláken NEZRYCHLÍ, spíš naopak – "
            f"nech na 1, pokud si to sám nepřeměříš s jiným enginem/HW.\n"
            f"• Hash tabulka: na rychlost prakticky nemá vliv.\n"
            f"• Paralelních enginů (jen dávkový rozbor na kartě Přesnost): každý "
            f"engine počítá jinou partii najednou. Tady zrychlení reálné je, ale "
            f"s klesající návratností (~1,5× při 2, ~2,3× při 5, strop kolem 3×) – "
            f"a žere odpovídající počet jader.")
        info.setWordWrap(True)
        form.addRow(info)
        spin_threads = QSpinBox()
        spin_threads.setRange(1, max(1, cpu))
        spin_threads.setValue(int(self.config.get("engine_threads") or default_threads()))
        form.addRow("Vlákna (1 engine):", spin_threads)
        spin_hash = QSpinBox()
        spin_hash.setRange(16, 8192)
        spin_hash.setSingleStep(64)
        spin_hash.setValue(int(self.config.get("engine_hash_mb") or DEFAULT_HASH_MB))
        spin_hash.setSuffix(" MB")
        form.addRow("Hash tabulka:", spin_hash)
        spin_parallel = QSpinBox()
        spin_parallel.setRange(1, max(1, cpu))
        spin_parallel.setValue(clamp_parallel(self.config.get("engine_parallel", DEFAULT_PARALLEL)))
        form.addRow("Paralelních enginů (dávka):", spin_parallel)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.Accepted:
            return
        self.config["engine_threads"] = spin_threads.value()
        self.config["engine_hash_mb"] = spin_hash.value()
        self.config["engine_parallel"] = spin_parallel.value()
        save_config(self.config)
        QMessageBox.information(
            self, "Uloženo",
            f"Vlákna: {spin_threads.value()}, Hash: {spin_hash.value()} MB, "
            f"paralelních enginů: {spin_parallel.value()}.\n"
            f"Projeví se při dalším spuštění rozboru (živá analýza pozice se "
            f"restartuje hned).")
        if self.chk_engine.isChecked():
            self._restart_engine()

    def _update_engine_status(self) -> None:
        path = self.config.get("engine_path", "")
        if path and os.path.exists(path):
            self.engine_status.setText(f"Engine: {os.path.basename(path)}")
        elif path:
            self.engine_status.setText(f"Engine: cesta neexistuje ({path})")
        else:
            self.engine_status.setText("Engine: nenastaven – Engine → Nastavit cestu…")

    def _toggle_engine(self, checked: bool) -> None:
        if checked:
            path = self.config.get("engine_path", "")
            if not path or not os.path.exists(path):
                QMessageBox.information(
                    self, "Engine není nastaven",
                    "Nejprve nastav cestu ke Stockfish/UCI enginu: menu Engine → Nastavit cestu…")
                self.chk_engine.setChecked(False)
                return
            self._restart_engine()
        else:
            self._stop_engine()

    def _clear_analysis_cache(self) -> None:
        probe = self._acc_cache or AnalysisCache()
        try:
            n = len(probe._data)
        except Exception:
            n = 0
        if n == 0:
            QMessageBox.information(self, "Cache rozborů",
                                   "Cache je prázdná – žádné uložené rozbory partií.")
            return
        if QMessageBox.question(
                self, "Smazat cache rozborů",
                f"Smazat uložené rozbory partií enginem?\n\n"
                f"Soubor analysis_cache.json.gz · {n} partií.\n\n"
                f"Rozbory na kartách Přesnost, Partie a Taktika se pak budou muset "
                f"spočítat znovu (engine). Nevratné.") != QMessageBox.Yes:
            return
        if self._acc_batch is not None and self._acc_batch.isRunning():
            QMessageBox.information(self, "Cache rozborů",
                                   "Nejdřív nech doběhnout (nebo zastav) běžící rozbor "
                                   "na kartě Přesnost.")
            return
        removed = (self._acc_cache or probe).clear()
        self._acc_cache = None
        self._acc_last_result = None
        self._game_eval = None
        self._tactics_all = []
        self._tactics_shown = []
        self._tactics_cur = None
        if hasattr(self, "move_table"):
            self._rebuild_move_table()
            self._highlight_current_move()
        self._update_eval_bar()
        if hasattr(self, "ga_summary"):
            self.ga_summary.setText("")
            self.btn_game_analyze.setText("⚙ Rozebrat partii enginem")
        if hasattr(self, "crit_row"):
            self.crit_row.hide()
            self._crit_plies = []
        self._invalidate_analyses()
        self.statusBar().showMessage(f"Cache rozborů smazána ({removed} partií).", 8000)

    def _clear_tactics_progress(self) -> None:
        n = 0
        try:
            n = len(self._tactics_store._data)
        except Exception:
            pass
        if n == 0:
            QMessageBox.information(self, "Taktické úlohy",
                                   "Žádný uložený postup (tactics_progress.json).")
            return
        if QMessageBox.question(
                self, "Smazat postup v taktických úlohách",
                f"Smazat celý postup v taktických úlohách?\n\n"
                f"Soubor tactics_progress.json · {n} záznamů (viděno/vyřešeno, "
                f"hodnocení užitečnosti, hvězdičky, plán opakování).\n\n"
                f"Úlohy samotné zůstanou – odvodí se znovu z rozboru. Nevratné."
                ) != QMessageBox.Yes:
            return
        removed = self._tactics_store.clear()
        if hasattr(self, "tac_table"):
            self._tactics_cur = None
            self.tac_feedback.setText("")
            self._apply_tactics_filter()      # přefiltruje (stavy jsou teď „neviděno")
        self.statusBar().showMessage(
            f"Postup v taktických úlohách smazán ({removed} záznamů).", 8000)

    def _use_wdl(self) -> bool:
        return bool(self.config.get("use_wdl", True))

    def _on_toggle_wdl(self, on: bool) -> None:
        self.config["use_wdl"] = bool(on)
        save_config(self.config)
        self.statusBar().showMessage(
            ("Reálné W/D/L zapnuto." if on else "Reálné W/D/L vypnuto (sigmoida z cp).")
            + " Projeví se po novém spuštění rozboru (↻ Rozebrat znovu).", 8000)

    def _engine_perf_kwargs(self) -> dict:
        """Vlákna/hash pro engine.configure() – None necháno na engine_perf.py
        (automatický výchozí podle CPU), přepsáno jen když je uloženo v configu
        (menu Engine → Nastavit výkon enginu…)."""
        return {
            "threads": self.config.get("engine_threads") or None,
            "hash_mb": self.config.get("engine_hash_mb") or None,
        }

    def _restart_engine(self) -> None:
        self._stop_engine()
        path = self.config.get("engine_path", "")
        self.engine = EngineWorker(path, multipv=self.spin_multipv.value(),
                                   **self._engine_perf_kwargs())
        self.engine.info_ready.connect(self._on_engine_info)
        self.engine.engine_error.connect(self._on_engine_error)
        self.engine.engine_started.connect(self._on_engine_started)
        self.engine.start()
        if self.game is not None:
            self.engine.set_position(self.current_board())

    def _stop_engine(self) -> None:
        if self.engine is not None:
            self.engine.stop()
            self.engine.wait(3000)
            self.engine = None
        self.engine_lines.clear()
        self.board.set_arrows([])
        self._clear_variation()
        self._update_eval_bar()

    def _on_multipv_changed(self, value: int) -> None:
        if self.engine is not None:
            self.engine.set_multipv(value)

    def _on_engine_started(self, name: str) -> None:
        self._engine_name = name
        self.engine_status.setText(f"Engine běží: {name}")

    def _on_engine_error(self, msg: str) -> None:
        QMessageBox.warning(self, "Engine", msg)
        self.chk_engine.setChecked(False)
        self._stop_engine()
        self._update_engine_status()

    def _on_engine_info(self, info: dict) -> None:
        if info.get("gameover"):
            self.engine_lines.clear()
            self.engine_status.setText("Pozice je konečná (mat/pat/remíza).")
            self.board.set_arrows([])
            self._clear_variation()
            self.eval_bar.clear()
            return

        # ignoruj výsledky pro už neaktuální pozici
        cur = self.current_board()
        if _board_key(info.get("fen", "")) != _board_key(cur.fen()):
            return

        lines0 = info.get("lines") or []
        if lines0:
            self.eval_bar.set_eval(cp=lines0[0].get("score_cp"),
                                   mate=lines0[0].get("score_mate"))

        lines = info.get("lines", [])
        depth = info.get("depth", 0)
        nps = info.get("nps", 0)
        head = f"hloubka {depth}   {nps / 1000:.0f} kN/s" if nps else f"hloubka {depth}"
        self.engine_status.setText(f"{self._engine_name} — {head}")

        base_fen = info.get("fen", cur.fen())
        prev_row = self.engine_lines.currentRow()
        self.engine_lines.blockSignals(True)
        self.engine_lines.clear()
        for i, ln in enumerate(lines, 1):
            item = QListWidgetItem(f"{i}.  [{ln['score']}]   {ln['pv']}")
            item.setData(LINE_ROLE, {"pv_uci": ln.get("pv_uci", []), "base_fen": base_fen})
            self.engine_lines.addItem(item)
        if 0 <= prev_row < self.engine_lines.count():
            self.engine_lines.setCurrentRow(prev_row)
        self.engine_lines.blockSignals(False)

        self._draw_engine_arrows(lines)

        # živě aktualizuj přehrávanou variantu, pokud engine prohloubil linii
        if self._var_line_idx is not None and 0 <= self._var_line_idx < len(lines):
            new_pv = lines[self._var_line_idx].get("pv_uci", [])
            if new_pv != self._var_pv_cache:
                self._load_variation(self._var_line_idx, keep_index=True)

    def _draw_engine_arrows(self, lines: list[dict]) -> None:
        if not self.chk_arrows.isChecked() or not lines:
            self.board.set_arrows([])
            return
        arrows = []
        for i, ln in enumerate(lines):
            uci = ln.get("move")
            if not uci:
                continue
            try:
                mv = chess.Move.from_uci(uci)
            except ValueError:
                continue
            color = "green" if i == 0 else "blue"
            arrows.append(chess.svg.Arrow(mv.from_square, mv.to_square, color=color))
        self.board.set_arrows(arrows)

    # ------------------------------------------ rozbor hráče (strom + heatmapa)
    def _make_color_combo(self, on_change) -> QComboBox:
        cmb = QComboBox()
        for lbl, val in _COLOR_CHOICES:
            cmb.addItem(lbl, val)
        cmb.currentIndexChanged.connect(lambda *_: on_change())
        return cmb

    @staticmethod
    def _color_label(cmb: QComboBox) -> str:
        return cmb.currentText()

    def _refresh_player_combo(self) -> None:
        self._player_counts = collect_players(self.games)
        self.cmb_player.blockSignals(True)
        self.cmb_player.clear()
        for name, cnt in self._player_counts.most_common():
            self.cmb_player.addItem(f"{name}  ({cnt})", name)
        self.cmb_player.blockSignals(False)
        self.cmb_player.setEnabled(self.cmb_player.count() > 0)

    def _sync_player_to_game(self) -> None:
        if not self.game or not self.game.headers:
            return
        white = (self.game.headers.get("White") or "").strip()
        black = (self.game.headers.get("Black") or "").strip()
        pick, col = white, "white"
        if black and self._player_counts.get(black, 0) > self._player_counts.get(white, 0):
            pick, col = black, "black"
        idx = self.cmb_player.findData(pick)
        if idx < 0:
            return
        self.cmb_player.blockSignals(True)
        self.cmb_player.setCurrentIndex(idx)
        self.cmb_player.blockSignals(False)
        for cmb in (self.cmb_tree_color, self.cmb_heat_color, self.cmb_op_color,
                    self.cmb_pat_color, self.cmb_acc_color):
            cmb.blockSignals(True)
            cidx = cmb.findData(col)
            if cidx >= 0:
                cmb.setCurrentIndex(cidx)
            cmb.blockSignals(False)

    def _on_player_changed(self, *_) -> None:
        self._apply_filters()   # elo filtr závisí na hráči; volá i update tree + invalidate

    # --------------------------- ruční spouštění rozborů na kartách -----------
    _ANALYSIS = {"heat": "_update_heatmap", "op": "_update_openings",
                 "eg": "_update_endgames", "pat": "_update_patterns"}

    def _analysis_btn(self, kind: str):
        return {"heat": self.btn_heat_run, "op": self.btn_op_run,
                "eg": self.btn_eg_run, "pat": self.btn_pat_run}[kind]

    def _set_run_btn(self, kind: str) -> None:
        btn = self._analysis_btn(kind)
        btn.setEnabled(bool(self.games and self.cmb_player.currentData()))
        btn.setText("↻ Přepočítat" if kind in self._analysis_ready else "▶ Spustit rozbor")

    def _run_analysis(self, kind: str) -> None:
        if not self.games or not self.cmb_player.currentData():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage("Počítám rozbor…")
        try:
            getattr(self, self._ANALYSIS[kind])()
        finally:
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
        self._analysis_ready.add(kind)
        self._set_run_btn(kind)

    def _reanalyze_if_ready(self, kind: str) -> None:
        if kind in self._analysis_ready:
            getattr(self, self._ANALYSIS[kind])()

    def _invalidate_analyses(self) -> None:
        """Rozbory na kartách jsou zastaralé – vyčisti a čekej na tlačítko."""
        self._analysis_ready.clear()
        have = bool(self.games and self.cmb_player.currentData())
        prompt = "Klikni na „▶ Spustit rozbor“." if have else "Načti PGN databázi a vyber hráče."
        if hasattr(self, "heatmap"):
            self.heatmap.set_data([0] * 64, chess.WHITE)
            self.heat_note.setText(prompt)
            self._set_run_btn("heat")
        if hasattr(self, "opening_stats_tree"):
            self.opening_stats_tree.clear()
            self.op_summary.setText(prompt)
            self._set_run_btn("op")
        if hasattr(self, "endgame_tree"):
            self.endgame_tree.clear()
            self.eg_summary.setText(prompt)
            self._set_run_btn("eg")
        if hasattr(self, "patterns_tree"):
            self.patterns_tree.clear()
            self.pat_summary.setText(prompt)
            self._set_run_btn("pat")
        if hasattr(self, "accuracy_tree") and (
                self._acc_batch is None or not self._acc_batch.isRunning()):
            self.accuracy_tree.clear()
            self.acc_summary.setText(prompt)
            self.btn_acc_run.setText("▶ Spustit rozbor")
            self.btn_acc_run.setEnabled(have)
        if hasattr(self, "tac_table"):
            self._tactics_all = []
            self._tactics_shown = []
            self._tactics_cur = None
            self.tac_table.setRowCount(0)
            self.tac_feedback.setText("")
            self.tac_prompt.setText("Vyber úlohu ze seznamu.")
            self._set_tactic_controls_enabled(False)
            self.tac_summary.setText(
                "Rozeber partie na kartě Přesnost, pak „↻ Najít úlohy“.")

    # ------------------------------------------------ rozbor partie enginem
    def _toggle_game_analysis(self) -> None:
        if self._game_analyzer is not None and self._game_analyzer.isRunning():
            self._cancel_game_analysis()
            self.btn_game_analyze.setText("⚙ Rozebrat partii enginem")
            self.ga_summary.setText("Rozbor přerušen.")
            return
        path = self.config.get("engine_path", "")
        if not path or not os.path.exists(path):
            QMessageBox.information(
                self, "Engine není nastaven",
                "Nejprve nastav cestu ke Stockfish/UCI enginu: menu Engine → Nastavit cestu…")
            return
        if not self.game or self.game.ply_count == 0:
            return
        self.config["ga_depth"] = self.spin_ga_depth.value()
        save_config(self.config)
        moves_uci = [m.uci() for m in self.game.moves]
        # parent=self drží C++ objekt naživu i po zahození Python reference –
        # jinak by se QThread mohl zničit ještě za běhu run() a spadlo by to
        ga = GameAnalyzer(path, list(self.game.boards), moves_uci,
                          depth=self.spin_ga_depth.value(), parent=self,
                          parallel=clamp_parallel(self.config.get("engine_parallel", 1)),
                          use_wdl=self._use_wdl(),
                          **self._engine_perf_kwargs())
        ga.progress.connect(self._on_ga_progress)
        ga.finished_ok.connect(self._on_ga_done)
        ga.failed.connect(self._on_ga_failed)
        ga.finished.connect(self._on_ga_thread_finished)
        ga.finished.connect(ga.deleteLater)
        self._game_analyzer = ga
        ga.start()
        self.btn_game_analyze.setText("■ Zastavit rozbor")
        self.ga_summary.setText("Rozbírám partii…")

    def _cancel_game_analysis(self) -> None:
        ga = self._game_analyzer
        if ga is not None and ga.isRunning():
            ga.stop()
            ga.wait()          # počkej, až run() doopravdy skončí (kvůli životnosti objektu)
        self._game_analyzer = None

    def _on_ga_thread_finished(self) -> None:
        """Vlákno doběhlo (run() se vrátil) – teď je bezpečné zahodit referenci."""
        self._game_analyzer = None

    def _on_ga_progress(self, done: int, total: int) -> None:
        self.ga_summary.setText(f"Rozbírám partii… {done}/{total} pozic")

    def _on_ga_failed(self, msg: str) -> None:
        self.btn_game_analyze.setText("⚙ Rozebrat partii enginem")
        self.ga_summary.setText(msg)
        if hasattr(self, "ga_legend"):
            self.ga_legend.hide()

    def _on_ga_done(self, result: dict) -> None:
        self._game_eval = result
        self.btn_game_analyze.setText("↻ Rozebrat znovu")
        aw, ab = result["acpl"]["white"], result["acpl"]["black"]
        cw, cb = result["counts"]["white"], result["counts"]["black"]

        def part(c):
            return f"{c[2]}× ?? · {c[1]}× ? · {c[0]}× ?!"
        qs = quick_summary(result["evals"], result.get("wdls"))
        acc, epl = qs["accuracy"], qs["ep_lost"]
        nply = len(result["evals"]) - 1
        wmoves = (nply + 1) // 2 or 1
        bmoves = nply // 2 or 1
        iw = composite_index(acc["white"], aw, cw[2] * 100 / wmoves, None)
        ib = composite_index(acc["black"], ab, cb[2] * 100 / bmoves, None)

        def pct(x):
            return f"{x:.1f} %" if x is not None else "–"

        def num(x):
            return f"{x:.0f}" if x is not None else "–"
        self.ga_summary.setText(
            f"Přesnost – bílý {pct(acc['white'])}, černý {pct(acc['black'])}.   "
            f"ACPL – bílý {aw}, černý {ab}.   "
            f"Index – bílý {num(iw)}, černý {num(ib)}.   "
            f"Ztráta v oček. bodech – bílý {epl['white']}, černý {epl['black']}.\n"
            f"Bílý: {part(cw)}  |  Černý: {part(cb)}   ·   "
            f"volatilita partie: {_volatility(result['evals'], result.get('wdls'))['mean_swing']:.1f} % "
            f"na půltah")
        self._update_ga_legend()
        self.ga_legend.show()
        self._rebuild_move_table()
        self._highlight_current_move()
        self._update_eval_bar()
        self._fill_crit_moments(result)

    # ------------------------------------------------ kritické momenty partie
    def _fill_crit_moments(self, result: dict) -> None:
        evals = result.get("evals") or []
        ws = win_series(evals, result.get("wdls"))
        swings = []                      # (ply_po_tahu, delta_pb_z_pohledu_hráče_na_tahu)
        for k in range(len(ws) - 1):
            mover_white = (k % 2 == 0)
            d = (ws[k + 1] - ws[k]) if mover_white else (ws[k] - ws[k + 1])
            swings.append((k + 1, d))     # ply k+1 = pozice po tahu k
        # největší výkyvy (oběma směry), aspoň 8 p.b., max 10
        top = sorted(swings, key=lambda x: -abs(x[1]))
        top = [t for t in top if abs(t[1]) >= 8.0][:10]
        top.sort(key=lambda x: x[0])
        self._crit_plies = [p for p, _ in top]
        self.cmb_crit.blockSignals(True)
        self.cmb_crit.clear()
        moves = self.game.moves if self.game else []
        for ply, d in top:
            k = ply - 1
            san = "?"
            try:
                san = self.game.boards[k].san(moves[k])
            except Exception:
                pass
            mv_no = (k // 2) + 1
            dot = "." if k % 2 == 0 else "…"
            sign = "▲" if d > 0 else "▼"
            self.cmb_crit.addItem(f"{mv_no}{dot} {san}   {sign} {abs(d):.0f} % pro hráče na tahu")
        self.cmb_crit.blockSignals(False)
        self.crit_row.setVisible(bool(self._crit_plies))

    def _on_crit_selected(self, idx: int) -> None:
        if 0 <= idx < len(self._crit_plies):
            self.set_ply(self._crit_plies[idx])

    def _step_crit(self, direction: int) -> None:
        if not self._crit_plies:
            return
        cur = self.cmb_crit.currentIndex()
        nxt = max(0, min(len(self._crit_plies) - 1, cur + direction))
        self.cmb_crit.setCurrentIndex(nxt)
        self.set_ply(self._crit_plies[nxt])

    def _open_guess_move(self) -> None:
        if not self.game or self.game.ply_count == 0:
            QMessageBox.information(self, "Hádej tah", "Nejdřív otevři nějakou partii.")
            return
        if not self._game_eval or not self._game_eval.get("bestmoves"):
            QMessageBox.information(
                self, "Hádej tah",
                "Nejdřív partii rozeber enginem (tlačítko „⚙ Rozebrat partii enginem“) "
                "– „Hádej tah“ z toho rozboru čte nejlepší tahy.")
            return
        path = self.config.get("engine_path", "")
        if not path or not os.path.exists(path):
            QMessageBox.information(self, "Hádej tah",
                                   "Není nastaven engine (menu Engine → Nastavit cestu…).")
            return
        players = {self.game.headers.get("White", "bílý"): chess.WHITE,
                   self.game.headers.get("Black", "černý"): chess.BLACK}
        cur = self.cmb_player.currentData()
        default = 0
        names = list(players.keys())
        if cur in players:
            default = names.index(cur)
        name, ok = QInputDialog.getItem(
            self, "Hádej tah", "Za koho hádáš tahy:", names, default, False)
        if not ok:
            return
        side = players[name]
        n_moves = (self.game.ply_count + 1) // 2
        start, ok = QInputDialog.getInt(
            self, "Hádej tah", "Od kolikátého tahu:", 1, 1, max(1, n_moves), 1)
        if not ok:
            return
        dlg = GuessMoveDialog(self.game, self._game_eval, path,
                              int(self.config.get("ga_depth", 12)), side, start, self,
                              use_wdl=self._use_wdl())
        dlg.exec()

    # ------------------------------------------- styl značení tahů (lichess/chess.com)
    def _active_style(self) -> str:
        return self.cmb_ga_style.currentData() or "lichess"

    def _active_marks(self):
        """Vrátí (marks, TEXT, FG, BG) pro aktuálně vybraný styl – ``marks`` je
        {ply: symbol} z posledního rozboru (``self._game_eval``)."""
        ev = self._game_eval or {}
        if self._active_style() == "chesscom":
            return ev.get("cc_marks", {}), CC_MARK_TEXT, CC_MARK_FG, CC_MARK_BG
        return ev.get("marks", {}), MARK_TEXT, MARK_FG, MARK_BG

    def _update_ga_legend(self) -> None:
        kinds = CC_MARK_KINDS if self._active_style() == "chesscom" else MARK_KINDS
        self.ga_legend.setText("  ".join(
            f"<span style='background:{bg};color:{fg};"
            f"padding:1px 5px;border-radius:3px'>&nbsp;{mk}&nbsp;</span> {lbl}"
            for mk, lbl, fg, bg in kinds))

    def _on_ga_style_changed(self, _idx: int) -> None:
        self.config["ga_style"] = self._active_style()
        save_config(self.config)
        if not self._game_eval:
            return
        self._update_ga_legend()
        self._rebuild_move_table()
        self._highlight_current_move()

    # ------------------------------------------- dávkový rozbor přesnosti (karta)
    def _toggle_accuracy_batch(self) -> None:
        if self._acc_batch is not None and self._acc_batch.isRunning():
            self._cancel_accuracy_batch()
            self.btn_acc_run.setText("▶ Spustit rozbor")
            self.acc_summary.setText("Rozbor přerušen.")
            self.acc_progress.setVisible(False)
            return
        path = self.config.get("engine_path", "")
        if not path or not os.path.exists(path):
            QMessageBox.information(
                self, "Engine není nastaven",
                "Nejprve nastav cestu ke Stockfish/UCI enginu: menu Engine → Nastavit cestu…")
            return
        player = self.cmb_player.currentData()
        if not self.games or not player:
            return
        colors = self.cmb_acc_color.currentData()
        matched = [
            (gi, g) for gi, g in enumerate(self.games)
            if (self._filter_keep is None or self._filter_keep(g))
            and game_matches(g, player, colors)
        ]
        if not matched:
            self.acc_summary.setText(f"{player}: žádné partie k rozboru.")
            return
        matched.sort(key=lambda t: (game_year(t[1]) or 0), reverse=True)
        cap = self.spin_acc_cap.value()
        jobs = [(gi, g, (g.headers.get("White", "").strip() == player))
                for gi, g in matched[:cap]]
        thorough = self.chk_acc_thorough.isChecked()
        self.config["acc_depth"] = self.spin_acc_depth.value()
        self.config["acc_cap"] = cap
        self.config["acc_thorough"] = thorough
        save_config(self.config)
        if self._acc_cache is None:
            self._acc_cache = AnalysisCache()
        b = AccuracyBatch(path, jobs, self.spin_acc_depth.value(),
                          self._acc_cache, thorough=thorough, parent=self,
                          parallel=self.config.get("engine_parallel", 1),
                          use_wdl=self._use_wdl(),
                          **self._engine_perf_kwargs())
        b.progress.connect(self._on_acc_progress)
        b.finished_ok.connect(self._on_acc_done)
        b.failed.connect(self._on_acc_failed)
        b.finished.connect(self._on_acc_thread_finished)
        b.finished.connect(b.deleteLater)
        self._acc_batch = b
        self.acc_progress.setRange(0, len(jobs))
        self.acc_progress.setValue(0)
        self.acc_progress.setVisible(True)
        b.start()
        self.btn_acc_run.setText("■ Zastavit rozbor")
        self.acc_summary.setText(
            f"Rozbírám {len(jobs)} partií hráče {player} (z {len(matched)})…")

    def _cancel_accuracy_batch(self) -> None:
        b = self._acc_batch
        if b is not None and b.isRunning():
            b.stop()
            b.wait()
        self._acc_batch = None

    def _on_acc_thread_finished(self) -> None:
        self._acc_batch = None

    def _on_acc_progress(self, done: int, total: int, label: str) -> None:
        self.acc_progress.setValue(done)
        if label:
            self.acc_summary.setText(f"{done}/{total} – {label}")

    def _on_acc_failed(self, msg: str) -> None:
        self.btn_acc_run.setText("▶ Spustit rozbor")
        self.acc_progress.setVisible(False)
        self.acc_summary.setText(msg)

    def _on_acc_done(self, result: dict) -> None:
        self.btn_acc_run.setText("↻ Rozebrat znovu")
        self.acc_progress.setVisible(False)
        self._acc_last_result = result
        self._fill_accuracy_tree(result)
        self._refresh_tactics_after_analysis()

    @staticmethod
    def _acc_brush(value: float, lo: float, hi: float, invert: bool = False) -> QBrush:
        t = (value - lo) / (hi - lo) if hi != lo else 0.0
        t = max(0.0, min(1.0, t))
        return _score_brush(1.0 - t if invert else t)

    @staticmethod
    def _fmt(v, spec: str) -> str:
        return format(v, spec) if v is not None else "–"

    def _acc_row(self, label: str, st: dict) -> QTreeWidgetItem:
        acc, idx, ipr = st["accuracy"], st.get("index"), st.get("ipr")
        row = QTreeWidgetItem([
            label, str(st["games"]),
            f"{acc:.1f}" if acc is not None else "–",
            str(st["acpl"]),
            f"{st['ep_per_game']:.2f}",
            f"{st['blund_100']:.1f}",
            f"{st['t1']:.0f}" if st["t1"] is not None else "–",
            f"{idx:.0f}" if idx is not None else "–",
            f"{ipr}" if ipr is not None else "–",
            self._fmt(st.get("volatility"), ".1f"),
            self._fmt(st.get("reversals"), ".1f"),
            self._fmt(st.get("sharpness"), ".2f"),
            self._fmt(st.get("complexity"), ".2f"),
            self._fmt(st.get("tact"), ".0f"),
        ])
        row.setToolTip(5, f"na 100 tahů: {st['blund_100']:.1f}× ?? · "
                          f"{st['mist_100']:.1f}× ? · {st['inacc_100']:.1f}× ?!")
        if acc is not None:
            row.setBackground(2, self._acc_brush(acc, 55, 95))
        row.setBackground(3, self._acc_brush(st["acpl"], 10, 90, invert=True))
        if idx is not None:
            row.setBackground(7, self._acc_brush(idx, 45, 90))
        if st.get("tact") is not None:
            row.setBackground(13, self._acc_brush(st["tact"], 40, 85))
        for c in range(1, 14):
            row.setTextAlignment(c, Qt.AlignCenter)
        return row

    @staticmethod
    def _add_kv_section(tree: QTreeWidget, cols: int, title: str, lines: list) -> None:
        """Skupina s volným textem po řádcích (charakter partií, dotahování apod.)."""
        if not lines:
            return
        gitem = QTreeWidgetItem([title] + [""] * (cols - 1))
        fnt = gitem.font(0)
        fnt.setBold(True)
        gitem.setFont(0, fnt)
        gitem.setFirstColumnSpanned(True)
        tree.addTopLevelItem(gitem)
        for line in lines:
            child = QTreeWidgetItem([line] + [""] * (cols - 1))
            child.setFirstColumnSpanned(True)
            gitem.addChild(child)
        gitem.setExpanded(True)

    def _fill_accuracy_tree(self, result: dict) -> None:
        tree = self.accuracy_tree
        tree.clear()
        cols = tree.columnCount()
        n, dep = result["n_games"], result["depth"]
        part = "  (přerušeno)" if result.get("partial") else ""
        info = result.get("ipr_info") or {}
        ipr_txt = ""
        if info.get("value") is not None:
            if info.get("anchor") is not None:
                rec = ""
                if info.get("recent") is not None and info["recent"] != info["value"]:
                    d = info["recent"] - info["anchor"]
                    rec = (f" Poslední partie ≈ {info['recent']} "
                           f"({'+' if d >= 0 else ''}{d}).")
                ipr_txt = (f"   IPR ≈ {info['value']} "
                           f"({info['method']}, {info['n_rated']} hodnocených partií).{rec} "
                           f"Odchylky po barvě/období v tabulce ukazují formu.")
            else:
                mr = info.get("mean_rating")
                rel = (f", tvé průměrné Elo v rozboru {mr}" if mr else "")
                ipr_txt = (f"   IPR ≈ {info['value']} ({info['method']}{rel}).")
        self.acc_summary.setText(
            f"Rozebráno {n} z {result['requested']} partií, hloubka {dep}{part}.   "
            f"Přesnost = lichess vzorec (0–100); ACPL v setinách pěšce; "
            f"EP = ztráta v oček. bodech; T1 = shoda s nejlepším tahem; "
            f"Index = kompozit 0–100.{ipr_txt}")

        for group, rows in result["sections"]:
            gitem = QTreeWidgetItem([group] + [""] * (cols - 1))
            fnt = gitem.font(0)
            fnt.setBold(True)
            gitem.setFont(0, fnt)
            gitem.setFirstColumnSpanned(True)
            tree.addTopLevelItem(gitem)
            for label, st in rows:
                gitem.addChild(self._acc_row(label, st))
            gitem.setExpanded(True)

        self._add_kv_section(tree, cols, "Charakter partií", result.get("character") or [])
        self._add_kv_section(tree, cols, "Dotahování", result.get("conversion") or [])

        bril = [b for b in (result.get("brilliants") or []) if b["by_player"]]
        if bril:
            gitem = QTreeWidgetItem(
                [f"Brilantní tahy hráče ({len(bril)}) – dvojklik skočí na tah"]
                + [""] * (cols - 1))
            fnt = gitem.font(0)
            fnt.setBold(True)
            gitem.setFont(0, fnt)
            gitem.setFirstColumnSpanned(True)
            tree.addTopLevelItem(gitem)
            for b in bril:
                txt = f"{b['label']}   ·   {b['move_no']}. tah"
                child = QTreeWidgetItem([txt] + [""] * (cols - 1))
                child.setFirstColumnSpanned(True)
                child.setData(0, ACC_GAME_ROLE, b["gi"])
                child.setData(0, ACC_PLY_ROLE, b["ply"])
                gitem.addChild(child)
            gitem.setExpanded(len(bril) <= 40)

        pg = result.get("per_game") or []
        if pg:
            parent = QTreeWidgetItem(["Jednotlivé partie (dvojklik otevře)"]
                                     + [""] * (cols - 1))
            fnt = parent.font(0)
            fnt.setBold(True)
            parent.setFont(0, fnt)
            parent.setFirstColumnSpanned(True)
            tree.addTopLevelItem(parent)
            for r in pg:
                acc, idx, ipr = r["acc"], r.get("index"), r.get("ipr")
                ep, t1 = r.get("ep_lost"), r.get("t1")
                tp = r.get("tact_pct")
                it = QTreeWidgetItem([
                    r["label"], ("b" if r["is_white"] else "č"),
                    f"{acc:.1f}" if acc is not None else "–",
                    str(r["acpl"]),
                    f"{ep:.2f}" if ep is not None else "–",
                    str(r["blund"]),
                    f"{t1:.0f}" if t1 is not None else "–",
                    f"{idx:.0f}" if idx is not None else "–",
                    f"{ipr}" if ipr is not None else "–",
                    self._fmt(r.get("vol"), ".1f"),
                    self._fmt(r.get("reversals"), "d"),
                    self._fmt(r.get("sharp"), ".2f"),
                    self._fmt(r.get("cx"), ".2f"),
                    self._fmt(tp, ".0f")])
                it.setData(0, ACC_GAME_ROLE, r["gi"])
                if acc is not None:
                    it.setBackground(2, self._acc_brush(acc, 55, 95))
                it.setBackground(3, self._acc_brush(r["acpl"], 10, 90, invert=True))
                if idx is not None:
                    it.setBackground(7, self._acc_brush(idx, 45, 90))
                if tp is not None:
                    it.setBackground(13, self._acc_brush(tp, 40, 85))
                for c in range(1, 14):
                    it.setTextAlignment(c, Qt.AlignCenter)
                parent.addChild(it)
            parent.setExpanded(False)

    def _on_accuracy_item_activated(self, item, _column) -> None:
        gi = item.data(0, ACC_GAME_ROLE)
        if gi is not None:
            ply = item.data(0, ACC_PLY_ROLE)
            self._jump_to_game(int(gi), int(ply) if ply is not None else 0)

    def _update_opening_tree(self) -> None:
        if not hasattr(self, "opening_tree"):
            return
        self.opening_tree.clear()
        player = self.cmb_player.currentData()
        colors = self.cmb_tree_color.currentData()
        clabel = self._color_label(self.cmb_tree_color)
        if not self.games or not player:
            self.tree_summary.setText("Načti PGN databázi a vyber hráče.")
            return
        root = build_position_tree(
            self.games, player, colors, self.current_board(), self.spin_tree_depth.value(),
            keep=self._filter_keep)
        if root.games == 0:
            self.tree_summary.setText(
                f"Žádná partie hráče {player} ({clabel}) touto pozicí neprošla.")
            return
        wr = root.winrate
        extra = (f"  |  celkový winrate {wr * 100:.0f} %" if wr is not None else "")
        self.tree_summary.setText(
            f"{player} {clabel}: {root.games} partií touto pozicí  "
            f"(V {root.win} · R {root.draw} · P {root.loss}){extra}")
        self._op_pending = {}
        buckets = [(c.win + 0.5 * c.draw, c.decided) for c in root.children.values()
                  if c.decided > 0]
        self._op_tree_p0 = root.score if root.score is not None else 0.5
        self._op_tree_m = estimate_shrink_m(buckets) if len(buckets) >= 2 else 10.0
        self._add_tree_children(None, root, [])

    def _add_tree_children(self, parent_item, node, path: list[str]) -> None:
        """Přidá jen přímé potomky uzlu; hlubší tahy se dopočítají až po rozbalení."""
        for child in node.sorted_children():
            wr = child.winrate
            child_path = path + [child.move_uci]
            share = round(child.games * 100 / node.games) if node.games else 0
            w, d, lo = child.win, child.draw, child.loss
            item = QTreeWidgetItem([
                child.move_san,
                str(child.games),
                f"{wr * 100:.0f} %" if wr is not None else "–",
                "",
                f"{share} %",
            ])
            item.setData(0, PATH_ROLE, child_path)
            item.setData(3, WDL_ROLE, (w, d, lo))
            item.setToolTip(3, f"výhra {w} · remíza {d} · prohra {lo}"
                               f"  (z {w + d + lo} rozhodnutých)")
            item.setBackground(2, _score_brush(wr))
            pred = child.predicted_score(
                getattr(self, "_op_tree_p0", None), getattr(self, "_op_tree_m", 10.0))
            if pred is not None:
                item.setToolTip(2, f"Odhad skóre přes podstrom (Markovův řetězec, "
                                   f"stažený k průměru {self._op_tree_p0 * 100:.0f} %): "
                                   f"{pred * 100:.0f} %")
            for col in (1, 2, 4):
                item.setTextAlignment(col, Qt.AlignCenter)
            if parent_item is None:
                self.opening_tree.addTopLevelItem(item)
            else:
                parent_item.addChild(item)
            if child.children:
                item.addChild(QTreeWidgetItem(["…"]))   # zástupný, ať je vidět šipka
                self._op_pending[id(item)] = (item, child, child_path)

    def _on_op_item_expanded(self, item) -> None:
        pend = self._op_pending.pop(id(item), None)
        if pend is None:
            return
        _, node, path = pend
        item.takeChildren()
        self._add_tree_children(item, node, path)

    def _on_tree_move_activated(self, item, _column) -> None:
        path = item.data(0, PATH_ROLE)
        if not path:
            return
        board = self.current_board()
        moves: list[chess.Move] = []
        for uci in path:
            try:
                mv = chess.Move.from_uci(uci)
            except ValueError:
                break
            if mv not in board.legal_moves:
                break
            board.push(mv)
            moves.append(mv)
        if not moves:
            return
        if self.autoplay.isActive():
            self._toggle_autoplay()
        self.analysis_moves.extend(moves)
        self._after_position_change()

    def _update_heatmap(self) -> None:
        if not hasattr(self, "heatmap"):
            return
        orient = chess.BLACK if self.chk_heat_black.isChecked() else chess.WHITE
        player = self.cmb_player.currentData()
        colors = self.cmb_heat_color.currentData()
        if not self.games or not player:
            self.heatmap.set_data([0] * 64, orient)
            self.heat_note.setText("Načti PGN databázi a vyber hráče.")
            return
        who = self.cmb_heat_who.currentData()
        mode = self.cmb_heat_mode.currentData()
        phase = self.cmb_heat_phase.currentData()
        outcome = self.cmb_heat_outcome.currentData()
        counts, total = heatmap_counts(
            self.games, player, colors,
            piece_type=self.cmb_heat_piece.currentData(),
            mode=mode, keep=self._filter_keep,
            who=who, phase=phase, outcome=outcome,
        )
        self.heatmap.set_data(counts, orient)
        mode_txt = {"to": "kam táhne", "from": "odkud táhne",
                    "capture": "kde bere"}[mode]
        who_txt = "hráč" if who == "player" else "soupeř"
        phase_txt = {"all": "", "opening": ", zahájení",
                     "middle": ", střední hra", "endgame": ", koncovka"}[phase]
        out_txt = {"all": "", "win": ", jen výhry", "loss": ", jen prohry"}[outcome]
        self.heat_note.setText(
            f"{player} {self._color_label(self.cmb_heat_color)} – tahy, které dělal "
            f"{who_txt}{phase_txt}{out_txt}: {total} tahů ({mode_txt}). "
            f"Číslo v poli = kolikrát; tmavší = častěji.")

    # ------------------------------------------------- koncovky (kategorie)
    def _update_endgames(self) -> None:
        if not hasattr(self, "endgame_tree"):
            return
        self.endgame_tree.clear()
        player = self.cmb_player.currentData()
        if not self.games or not player:
            self.eg_summary.setText("Načti PGN databázi a vyber hráče.")
            return
        cats = analyze_endgames(self.games, player, keep=self._filter_keep)
        if not cats:
            self.eg_summary.setText(
                f"Žádná partie hráče {player} nedošla do koncovky.")
            return

        total_games = len({e.game_index for c in cats for e in c.entries})
        self.eg_summary.setText(
            f"{player} (obě barvy): {total_games} partií došlo do koncovky, rozřazeno do "
            f"{len(cats)} kategorií podle složení figur (partie může být ve více kategoriích); "
            f"uvnitř kategorie rozděleno podle počtu pěšců.   ({PIECE_LEGEND})\n"
            f"▲ / ▼ = kategorie se po BH korekci (FDR 5 %) významně liší od tvého "
            f"winrate ve všech koncovkách.")

        res_cz = {"win": "výhra", "draw": "remíza", "loss": "prohra"}
        all_eg = [e.result for c in cats for e in c.entries]
        p0 = _report_prior(all_eg, [])[0]
        w0, n0 = _overall_wr(all_eg)
        cat_m = _bucket_shrink_m([(None, [e.result for e in c.entries]) for c in cats])
        pawns_m = _bucket_shrink_m(
            [(None, [e.result for e in entries])
             for c in cats for _, entries in c.by_pawns()])
        cat_sig = _sig_flags([[e.result for e in c.entries] for c in cats], w0, n0)
        self.endgame_tree.setSortingEnabled(False)
        for ci, cat in enumerate(cats):
            cat_res = [e.result for e in cat.entries]
            cat_item = _stat_item(cat.label, cat_res, name_key=sort_key(cat.label),
                                  p0=p0, shrink_m=cat_m, sig=cat_sig[ci])
            fnt = cat_item.font(0)
            fnt.setBold(True)
            cat_item.setFont(0, fnt)
            self.endgame_tree.addTopLevelItem(cat_item)

            pw_rows = list(cat.by_pawns())
            pw_sig = _sig_flags([[e.result for e in ent] for _, ent in pw_rows], w0, n0)
            for pi, (pawns, entries) in enumerate(pw_rows):
                pit = _stat_item(pawns_cz(pawns), [e.result for e in entries], name_key=pawns,
                                 p0=p0, shrink_m=pawns_m, sig=pw_sig[pi])
                cat_item.addChild(pit)
                for e in sorted(entries, key=lambda x: x.game_index):
                    g = self.games[e.game_index]
                    lbl = (f"{g.headers.get('White', '?')} – {g.headers.get('Black', '?')}"
                           f"   ({res_cz.get(e.result, '–')}, od {e.enter_ply // 2 + 1}. tahu)")
                    game_item = SortableItem([lbl, "", "", ""])
                    game_item.setData(0, SORT_ROLE, lbl.casefold())
                    game_item.setData(0, EG_GAME_ROLE, (e.game_index, e.enter_ply))
                    pit.addChild(game_item)

        self._resort(self.endgame_tree)
        self.endgame_tree.collapseAll()  # kategorie sbalené, rozbal si co potřebuješ

    def _jump_to_game(self, game_index: int, ply: int) -> None:
        if not (0 <= game_index < len(self.games)):
            return
        if self.game_selector.currentIndex() != game_index:
            self.game_selector.setCurrentIndex(game_index)
        else:
            self._on_game_selected(game_index)
        self.set_ply(ply)
        self.top_tabs.setCurrentIndex(self._board_tab_index)

    def _on_endgame_item_activated(self, item, _column) -> None:
        data = item.data(0, EG_GAME_ROLE)
        if data:
            self._jump_to_game(*data)

    @staticmethod
    def _resort(tree: QTreeWidget) -> None:
        tree.setSortingEnabled(True)
        hdr = tree.header()
        col = max(0, hdr.sortIndicatorSection())
        tree.sortItems(col, hdr.sortIndicatorOrder())

    # ------------------------------------------------- zahájení (ECO)
    def _update_openings(self) -> None:
        if not hasattr(self, "opening_stats_tree"):
            return
        self.opening_stats_tree.clear()
        player = self.cmb_player.currentData()
        if not self.games or not player:
            self.op_summary.setText("Načti PGN databázi a vyber hráče.")
            return
        clabel = self._color_label(self.cmb_op_color)
        groups = analyze_openings(self.games, player, self.cmb_op_color.currentData(),
                                  keep=self._filter_keep)
        if not groups:
            self.op_summary.setText(f"{player} {clabel}: žádné partie.")
            return
        n_games = sum(len(v.entries) for g in groups for v in g.variations)
        n_var = sum(len(g.variations) for g in groups)
        div = repertoire_diversity(groups)
        book = personal_book_depth(self.games, player, self.cmb_op_color.currentData(),
                                   keep=self._filter_keep)
        self.op_summary.setText(
            f"{player} {clabel}: {n_games} partií, {len(groups)} ECO kódů, {n_var} variant.\n"
            f"{_repertoire_text(div, book)}\n"
            f"▲ / ▼ = zahájení se po BH korekci (FDR 5 %) významně liší od tvého "
            f"celkového winrate (p v tooltipu).")
        all_res = [r for g in groups for r in g.results()]
        p0 = _report_prior(all_res, [])[0]
        w0, n0 = _overall_wr(all_res)
        eco_m = _bucket_shrink_m([(None, g.results()) for g in groups])
        var_m = _bucket_shrink_m([(None, v.results()) for g in groups for v in g.variations])
        eco_sig = _sig_flags([g.results() for g in groups], w0, n0)
        var_sig = _sig_flags([v.results() for g in groups for v in g.variations], w0, n0)

        res_cz = {"win": "výhra", "draw": "remíza", "loss": "prohra"}
        self.opening_stats_tree.setSortingEnabled(False)
        _vi = 0
        for gi_grp, grp in enumerate(groups):
            eco_item = _stat_item(grp.label, grp.results(),
                                  name_key="zzz" if grp.code == "?" else grp.code,
                                  p0=p0, shrink_m=eco_m, sig=eco_sig[gi_grp])
            fnt = eco_item.font(0)
            fnt.setBold(True)
            eco_item.setFont(0, fnt)
            self.opening_stats_tree.addTopLevelItem(eco_item)

            for var in grp.variations:
                v_item = _stat_item(var.name, var.results(), name_key=sort_key(var.name),
                                    p0=p0, shrink_m=var_m, sig=var_sig[_vi])
                _vi += 1
                eco_item.addChild(v_item)
                for e in sorted(var.entries, key=lambda x: x.game_index):
                    gm = self.games[e.game_index]
                    tail = f", od {e.enter_ply // 2 + 1}. tahu" if e.enter_ply else ""
                    lbl = (f"{gm.headers.get('White', '?')} – {gm.headers.get('Black', '?')}"
                           f"   ({res_cz.get(e.result, '–')}{tail})")
                    game_item = SortableItem([lbl, "", "", ""])
                    game_item.setData(0, SORT_ROLE, lbl.casefold())
                    game_item.setData(0, OP_GAME_ROLE, (e.game_index, e.enter_ply))
                    v_item.addChild(game_item)

        self._resort(self.opening_stats_tree)
        self.opening_stats_tree.collapseAll()

    def _on_opening_item_activated(self, item, _column) -> None:
        data = item.data(0, OP_GAME_ROLE)
        if data:
            self._jump_to_game(*data)

    # ------------------------------------------------- nejpodivnější partie
    def _open_anomaly(self) -> None:
        player = self.cmb_player.currentData()
        if not self.games or not player:
            QMessageBox.information(self, "Nejpodivnější partie",
                                   "Načti PGN databázi a vyber hráče.")
            return
        colors = self.cmb_pat_color.currentData()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage("Hledám nejpodivnější partie…")
        QApplication.processEvents()
        try:
            rep = detect_anomalies(self.games, player, colors,
                                   keep=self._filter_keep, top=30)
        finally:
            QApplication.restoreOverrideCursor()
            self.statusBar().clearMessage()
        if rep.n_features == 0 or not rep.anomalies:
            QMessageBox.information(
                self, "Nejpodivnější partie",
                f"Málo partií pro analýzu (je jich {rep.n_games}, potřeba aspoň 25 "
                f"a dost různorodých).")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Nejpodivnější partie")
        dlg.resize(900, 620)
        lay = QVBoxLayout(dlg)
        head = QLabel(
            f"{rep.n_games} partií po filtru (vyřazeno {rep.n_skipped}: mimo tvé "
            f"Elo pásmo, rozhodnuté drtivou materiální převahou nebo miniaturky – "
            f"ty nejsou ke studiu zajímavé). {rep.n_features} vlastností. "
            f"„Divnost“ = χ² percentil Mahalanobisovy vzdálenosti od tvé běžné hry "
            f"(medián d² = {rep.median_d2}). Sloupec „proč“ = vlastnosti, co "
            f"k odlišnosti přispěly nejvíc (hodnota partie vs. tvůj medián). "
            f"Dvojklik otevře partii.")
        head.setWordWrap(True)
        head.setStyleSheet("color:#555;")
        lay.addWidget(head)
        t = QTableWidget(len(rep.anomalies), 5)
        t.setHorizontalHeaderLabels(["Partie", "Výsl.", "Divnost", "d²", "Proč"])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        for c, wd in ((1, 46), (2, 66), (3, 52)):
            t.setColumnWidth(c, wd)
        rmap = {"win": "V", "draw": "R", "loss": "P"}
        for i, a in enumerate(rep.anomalies):
            why = " · ".join(f"{lbl}: {val} (ty {med})" for lbl, val, med, _c in a.reasons)
            cells = [a.label, rmap.get(a.result, "?"), f"{a.weirdness * 100:.1f} %",
                     f"{a.d2:.0f}", why]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                if c in (1, 2, 3):
                    it.setTextAlignment(Qt.AlignCenter)
                it.setData(Qt.UserRole, a.game_index)
                t.setItem(i, c, it)
        t.itemDoubleClicked.connect(
            lambda item: (self._jump_to_game(int(item.data(Qt.UserRole)), 0), dlg.accept()))
        lay.addWidget(t, stretch=1)
        btn = QPushButton("Zavřít")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn, alignment=Qt.AlignRight)
        dlg.exec()

    # ------------------------------------------------- vzorce (statistiky)
    def _update_patterns(self) -> None:
        if not hasattr(self, "patterns_tree"):
            return
        self.patterns_tree.clear()
        player = self.cmb_player.currentData()
        if not self.games or not player:
            self.pat_summary.setText("Načti PGN databázi a vyber hráče.")
            return
        colors = self.cmb_pat_color.currentData()
        clabel = self._color_label(self.cmb_pat_color)
        report = analyze_patterns(self.games, player, colors, keep=self._filter_keep)
        if report.n_games == 0:
            self.pat_summary.setText(f"{player} {clabel}: žádné partie.")
            return
        self.pat_summary.setText(
            f"{player} {clabel}: {report.n_games} partií. "
            f"Barvu rozboru přepni vlevo; platí filtr databáze.")
        # celkový winrate: první rozklad prvního rozboru pokrývá každou partii
        # přesně jednou (žádný koš „None"), takže jde vzít jako celý soubor
        first_rows = (report.groups[0].breakdowns[0].rows
                     if report.groups and report.groups[0].breakdowns else [])
        all_results = [r for _, res in first_rows for r in res]
        p0 = _report_prior(all_results, [])[0]
        w0, n0 = _overall_wr(all_results)

        for group in report.groups:
            g_item = QTreeWidgetItem([group.title, "", "", ""])
            fnt = g_item.font(0)
            fnt.setBold(True)
            g_item.setFont(0, fnt)
            g_item.setFirstColumnSpanned(True)
            self.patterns_tree.addTopLevelItem(g_item)
            for bd in group.breakdowns:
                b_item = QTreeWidgetItem([bd.title, "", "", ""])
                bf = b_item.font(0)
                bf.setItalic(True)
                b_item.setFont(0, bf)
                b_item.setForeground(0, QBrush(QColor("#555")))
                if bd.hint:
                    b_item.setToolTip(0, bd.hint)
                g_item.addChild(b_item)
                bd_m = _bucket_shrink_m(bd.rows)
                sigs = _sig_flags([res for _, res in bd.rows], w0, n0)
                for i, (label, results) in enumerate(bd.rows):
                    b_item.addChild(_stat_item(label, results, name_key=label.casefold(),
                                               p0=p0, shrink_m=bd_m, sig=sigs[i]))
        self._add_kv_section(
            self.patterns_tree, 4, "Elo-adjusted výkonnost a štěstí",
            report.summary_lines + [
                "▲ / ▼ u winrate = koš se po Benjamini–Hochberg korekci (FDR 5 %) "
                "významně liší od tvého celkového winrate; p-hodnota je v tooltipu."])
        self.patterns_tree.expandAll()

    def _flip_boards(self) -> None:
        self.board.flip()
        if self.mini_board.orientation != self.board.orientation:
            self.mini_board.flip()

    # ------------------------------------------ přehrávání varianty (mini)
    def _set_variation_controls_enabled(self, on: bool) -> None:
        for b in (self.btn_var_start, self.btn_var_prev, self.btn_var_next, self.btn_var_end):
            b.setEnabled(on)

    def _clear_variation(self) -> None:
        self._var_base = None
        self._var_moves = []
        self._var_idx = 0
        self._var_line_idx = None
        self._var_pv_cache = []
        self.mini_board.set_position(chess.Board(), None)
        self.mini_board.set_arrows([])
        self.var_title.setText("Přehrání varianty – klikni na linii výše")
        self.var_pos_label.setText("–")
        self._set_variation_controls_enabled(False)

    def _on_line_selected(self, row: int) -> None:
        if row < 0 or row >= self.engine_lines.count():
            return
        self._load_variation(row, keep_index=False)

    def _load_variation(self, line_idx: int, *, keep_index: bool) -> None:
        item = self.engine_lines.item(line_idx)
        if item is None:
            return
        data = item.data(LINE_ROLE) or {}
        try:
            base = chess.Board(data.get("base_fen", chess.STARTING_FEN))
        except ValueError:
            return
        moves: list[chess.Move] = []
        probe = base.copy()
        for uci in data.get("pv_uci", []):
            try:
                mv = chess.Move.from_uci(uci)
            except ValueError:
                break
            if mv not in probe.legal_moves:
                break
            probe.push(mv)
            moves.append(mv)

        if self.mini_board.orientation != self.board.orientation:
            self.mini_board.flip()

        self._var_base = base
        self._var_moves = moves
        self._var_line_idx = line_idx
        self._var_pv_cache = list(data.get("pv_uci", []))
        if keep_index:
            self._var_idx = max(0, min(self._var_idx, len(moves)))
        else:
            self._var_idx = 0
        self._set_variation_controls_enabled(bool(moves))
        self._render_variation()

    def _var_go(self, where) -> None:
        if not self._var_moves:
            return
        if where == "start":
            self._var_idx = 0
        elif where == "end":
            self._var_idx = len(self._var_moves)
        else:
            self._var_idx = max(0, min(self._var_idx + int(where), len(self._var_moves)))
        self._render_variation()

    def _render_variation(self) -> None:
        if self._var_base is None:
            return
        board = self._var_base.copy()
        for mv in self._var_moves[:self._var_idx]:
            board.push(mv)
        last = self._var_moves[self._var_idx - 1] if self._var_idx > 0 else None
        self.mini_board.set_position(board, last)

        total = len(self._var_moves)
        if self._var_idx == 0:
            self.var_pos_label.setText(f"výchozí pozice  (0/{total})")
        else:
            tmp = self._var_base.copy()
            for mv in self._var_moves[:self._var_idx - 1]:
                tmp.push(mv)
            san = tmp.san(self._var_moves[self._var_idx - 1])
            num = tmp.fullmove_number
            dots = "." if tmp.turn == chess.WHITE else "…"
            self.var_pos_label.setText(f"{num}{dots} {san}   ({self._var_idx}/{total})")
        prefix = "Varianta"
        if self._var_line_idx is not None:
            prefix = f"Varianta {self._var_line_idx + 1}"
        self.var_title.setText(f"{prefix} – ◀ ▶ přehrává tahy")

    # ------------------------------------------------------------ zavření
    def closeEvent(self, event):  # noqa: N802
        self.autoplay.stop()
        self._stop_engine()
        self._cancel_game_analysis()
        self._cancel_accuracy_batch()
        loader = getattr(self, "_loader", None)
        if loader is not None and loader.isRunning():
            loader.wait(2000)
        super().closeEvent(event)
