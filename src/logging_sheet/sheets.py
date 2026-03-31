"""구글 스프레드시트 기록 - 일별 기록 + 사이클 요약 + 월간 백업."""

import logging
from datetime import date, datetime

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",  # 이 앱이 생성한 파일만 접근
]

DAILY_SHEET_NAME = "일별 기록"
CYCLE_SHEET_NAME = "사이클 요약"

DAILY_HEADERS = [
    "사이클", "날짜", "종목", "현재가", "평균단가", "보유수량",
    "LOC 평단가", "LOC 고가", "액션", "체결수량", "체결금액",
    "분할(사용/전체)", "수익률(%)", "USD/KRW", "원화수익률(%)",
    "평가금액", "실현손익", "비고",
]

CYCLE_HEADERS = [
    "사이클", "시작일", "종료일", "종목", "투입총액(USD)", "매도총액(USD)",
    "총수익(USD)", "총수익(KRW)", "수익률(%)", "사용분할", "종료사유",
]


class SheetsLogger:
    """구글 스프레드시트 기록 관리."""

    def __init__(self, spreadsheet_id: str, credentials_path: str):
        self.spreadsheet_id = spreadsheet_id
        self.credentials_path = credentials_path
        self._client: gspread.Client | None = None
        self._spreadsheet: gspread.Spreadsheet | None = None

    def _connect(self) -> None:
        """구글 시트에 연결한다."""
        if self._client is not None:
            return

        try:
            creds = Credentials.from_service_account_file(
                self.credentials_path, scopes=SCOPES
            )
            self._client = gspread.authorize(creds)
            self._spreadsheet = self._client.open_by_key(self.spreadsheet_id)
            logger.info("구글 스프레드시트 연결 성공")
        except Exception as e:
            logger.error(f"구글 시트 연결 실패: {e}")
            raise

    def _get_or_create_sheet(self, name: str, headers: list[str]) -> gspread.Worksheet:
        """시트를 가져오거나 생성한다. 헤더가 없으면 추가."""
        self._connect()

        try:
            ws = self._spreadsheet.worksheet(name)
        except gspread.exceptions.WorksheetNotFound:
            ws = self._spreadsheet.add_worksheet(title=name, rows=1000, cols=len(headers))
            logger.info(f"시트 '{name}' 생성")

        # 헤더 확인/추가
        try:
            first_row = ws.row_values(1)
            if not first_row:
                ws.append_row(headers)
        except Exception:
            ws.append_row(headers)

        return ws

    def log_daily(
        self,
        cycle_number: int,
        today: str,
        symbol: str,
        current_price: float,
        avg_price: float,
        quantity: int,
        loc_avg_price: float,
        loc_high_price: float,
        action: str,
        fill_qty: int,
        fill_amount: float,
        splits_used: float,
        num_splits: int,
        return_pct: float,
        usd_krw_rate: float,
        eval_amount: float,
        realized_pnl: float,
        notes: str = "",
    ) -> None:
        """일별 기록을 추가한다."""
        try:
            ws = self._get_or_create_sheet(DAILY_SHEET_NAME, DAILY_HEADERS)

            krw_return_pct = return_pct  # 환율 변동 무시 (동일 기준)

            row = [
                cycle_number,
                today,
                symbol,
                f"{current_price:.2f}",
                f"{avg_price:.2f}",
                quantity,
                f"{loc_avg_price:.2f}" if loc_avg_price > 0 else "-",
                f"{loc_high_price:.2f}" if loc_high_price > 0 else "-",
                action,
                fill_qty,
                f"{fill_amount:.2f}",
                f"{splits_used:.1f}/{num_splits}",
                f"{return_pct:.2f}",
                f"{usd_krw_rate:.2f}" if usd_krw_rate > 0 else "-",
                f"{krw_return_pct:.2f}" if usd_krw_rate > 0 else "-",
                f"{eval_amount:.2f}",
                f"{realized_pnl:.2f}",
                notes,
            ]

            ws.append_row(row, value_input_option="USER_ENTERED")
            logger.info(f"일별 기록 추가: {symbol} {today}")
        except Exception as e:
            logger.error(f"일별 기록 실패: {e}")

    def log_cycle_summary(
        self,
        cycle_number: int,
        start_date: str,
        end_date: str,
        symbol: str,
        total_invested: float,
        total_sold: float,
        profit_usd: float,
        usd_krw_rate: float,
        return_pct: float,
        splits_used: float,
        num_splits: int,
        end_reason: str,
    ) -> None:
        """사이클 요약을 기록한다."""
        try:
            ws = self._get_or_create_sheet(CYCLE_SHEET_NAME, CYCLE_HEADERS)

            profit_krw = profit_usd * usd_krw_rate if usd_krw_rate > 0 else 0

            row = [
                cycle_number,
                start_date,
                end_date,
                symbol,
                f"{total_invested:.2f}",
                f"{total_sold:.2f}",
                f"{profit_usd:+.2f}",
                f"{profit_krw:+.0f}" if profit_krw != 0 else "-",
                f"{return_pct:+.2f}",
                f"{splits_used:.1f}/{num_splits}",
                end_reason,
            ]

            ws.append_row(row, value_input_option="USER_ENTERED")
            logger.info(f"사이클 요약 기록: {symbol} 사이클#{cycle_number}")
        except Exception as e:
            logger.error(f"사이클 요약 기록 실패: {e}")

    def create_monthly_backup(self) -> None:
        """현재 스프레드시트의 시트들을 백업 탭으로 복사한다."""
        try:
            self._connect()

            now = datetime.now()
            backup_suffix = now.strftime("%Y-%m")

            for sheet_name in [DAILY_SHEET_NAME, CYCLE_SHEET_NAME]:
                try:
                    source_ws = self._spreadsheet.worksheet(sheet_name)
                    backup_name = f"{sheet_name}_백업_{backup_suffix}"

                    # 이미 백업 존재하면 스킵
                    try:
                        self._spreadsheet.worksheet(backup_name)
                        logger.info(f"백업 '{backup_name}' 이미 존재, 스킵")
                        continue
                    except gspread.exceptions.WorksheetNotFound:
                        pass

                    source_ws.copy_to(self.spreadsheet_id)
                    # 복사된 시트 이름 변경
                    worksheets = self._spreadsheet.worksheets()
                    copied = worksheets[-1]  # 가장 마지막에 추가됨
                    copied.update_title(backup_name)

                    logger.info(f"월간 백업 생성: {backup_name}")
                except gspread.exceptions.WorksheetNotFound:
                    logger.warning(f"시트 '{sheet_name}' 없음, 백업 스킵")

        except Exception as e:
            logger.error(f"월간 백업 실패: {e}")
