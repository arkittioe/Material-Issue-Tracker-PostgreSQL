# file: data/project_service.py

import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Callable

import pandas as pd
from sqlalchemy import distinct, func, desc
from sqlalchemy.orm import Session, joinedload

from models import Project, MTOItem, MIVRecord, MTOProgress


class ProjectService:
    """
    سرویس مدیریت پروژه‌ها و داده‌های مرتبط (MTO, MIV, Progress Reports).
    """

    def __init__(self, session_factory: Callable[[], Session]):
        self.session_factory = session_factory

    # ------------------------------------------------------
    # CRUD برای Project
    # ------------------------------------------------------

    def get_or_create_project(self, session: Session, project_name: str) -> Project:
        project = session.query(Project).filter(Project.name == project_name).first()
        if not project:
            project = Project(name=project_name)
            session.add(project)
            session.flush()
        return project

    def get_all_projects(self) -> List[Project]:
        session = self.session_factory()
        try:
            return session.query(Project).order_by(Project.name).all()
        except Exception as e:
            logging.error(f"خطا در واکشی لیست پروژه‌ها: {e}")
            return []
        finally:
            session.close()

    def get_project_by_name(self, project_name: str) -> Optional[Project]:
        session = self.session_factory()
        try:
            return session.query(Project).filter(Project.name == project_name).first()
        except Exception as e:
            logging.error(f"خطا در واکشی پروژه با نام {project_name}: {e}")
            return None
        finally:
            session.close()

    def get_lines_for_project(self, project_id: int) -> List[str]:
        session = self.session_factory()
        try:
            mto_lines = {l[0] for l in session.query(distinct(MTOItem.line_no))
            .filter(MTOItem.project_id == project_id).all() if l[0]}
            miv_lines = {l[0] for l in session.query(distinct(MIVRecord.line_no))
            .filter(MIVRecord.project_id == project_id).all() if l[0]}
            return sorted(mto_lines | miv_lines)
        except Exception as e:
            logging.error(f"خطا در واکشی خطوط پروژه {project_id}: {e}")
            return []
        finally:
            session.close()

    # ------------------------------------------------------
    # متدهای پیشرفت و گزارش
    # ------------------------------------------------------

    def get_project_progress(self, project_id: int) -> Dict[str, Any]:
        """
        محاسبه میانگین پیشرفت کل پروژه بر اساس میانگین پیشرفت همه خطوط.
        """
        session = self.session_factory()
        try:
            progress_data = self.get_project_line_status_list(project_id)
            if not progress_data:
                return {"progress": 0, "lines": 0}
            avg_progress = sum(l['Progress (%)'] for l in progress_data) / len(progress_data)
            return {"progress": round(avg_progress, 2), "lines": len(progress_data)}
        finally:
            session.close()

    def get_line_progress(self, project_id: int, line_no: str) -> float:
        """
        محاسبه درصد پیشرفت یک خط بر اساس مقادیر MTOProgress.
        """
        session = self.session_factory()
        try:
            items = session.query(MTOProgress).filter(
                MTOProgress.project_id == project_id,
                MTOProgress.line_no == line_no
            ).all()
            if not items:
                return 0
            total_qty = sum(i.total_qty or 0 for i in items)
            if total_qty == 0:
                return 0
            used_qty = sum(i.used_qty or 0 for i in items)
            return round((used_qty / total_qty) * 100, 2)
        finally:
            session.close()

    def get_enriched_line_progress(self, project_id: int, line_no: str, readonly: bool = True) -> List[Dict[str, Any]]:
        """
        بازگرداندن داده‌های پیشرفت هر آیتم MTO در یک خط.
        پارامتر readonly برای سازگاری با کد قدیمی اضافه شده
        """
        session = self.session_factory()
        try:
            # ابتدا مطمئن شویم که MTOProgress برای این خط وجود دارد
            if not readonly:
                self.initialize_mto_progress_for_line(project_id, line_no)

            # Join با MTOItem برای گرفتن اطلاعات اضافی
            items = session.query(MTOProgress, MTOItem).join(
                MTOItem, MTOProgress.mto_item_id == MTOItem.id
            ).filter(
                MTOProgress.project_id == project_id,
                MTOProgress.line_no == line_no
            ).all()

            result = []
            for progress, mto_item in items:
                item_data = {
                    "mto_item_id": mto_item.id,
                    "Item Code": progress.item_code or mto_item.item_code,
                    "Description": progress.description or mto_item.description,
                    "Total Qty": progress.total_qty,
                    "Used Qty": progress.used_qty,
                    "Remaining Qty": progress.remaining_qty or ((progress.total_qty or 0) - (progress.used_qty or 0)),
                    "Unit": progress.unit or mto_item.unit,
                    "Type": mto_item.item_type,
                    "Bore": mto_item.p1_bore_in,
                    "progress": round((progress.used_qty or 0) / (progress.total_qty or 1) * 100, 2)
                }
                result.append(item_data)

            return result
        finally:
            session.close()

    def is_line_complete(self, project_id: int, line_no: str) -> bool:
        """
        بررسی می‌کند که آیا همه آیتم‌های MTO در یک خطا مصرف کامل شده‌اند.
        """
        session = self.session_factory()
        try:
            items = session.query(MTOProgress).filter(
                MTOProgress.project_id == project_id,
                MTOProgress.line_no == line_no
            ).all()
            return all((i.used_qty or 0) >= (i.total_qty or 0) for i in items)
        finally:
            session.close()

    def get_project_line_status_list(self, project_id: int) -> List[Dict[str, Any]]:
        """
        لیستی از خطوط پروژه به همراه درصد پیشرفت هر کدام.
        """
        lines = self.get_lines_for_project(project_id)
        return [{"Line No": l, "Progress (%)": self.get_line_progress(project_id, l)}
                for l in lines]

    def get_project_analytics(self, project_id: int) -> Dict[str, Any]:
        """
        محاسبات و آمار تجمیعی پروژه (مجموع آیتم‌ها، پیشرفت متوسط و ...).
        """
        status_list = self.get_project_line_status_list(project_id)
        return {
            "total_lines": len(status_list),
            "average_progress": round(sum(s["Progress (%)"] for s in status_list) / len(status_list), 2)
            if status_list else 0
        }

    def generate_project_report(self, project_id: int) -> pd.DataFrame:
        """
        گزارش کامل پروژه از داده‌های MTOProgress.
        """
        session = self.session_factory()
        try:
            # Join با MTOItem برای اطلاعات کامل
            items = session.query(MTOProgress, MTOItem).join(
                MTOItem, MTOProgress.mto_item_id == MTOItem.id
            ).filter(MTOProgress.project_id == project_id).all()

            data = [{
                "line_no": progress.line_no,
                "item_desc": progress.description or mto_item.description,
                "qty": progress.total_qty,
                "used_qty": progress.used_qty,
                "progress": round((progress.used_qty or 0) / (progress.total_qty or 1) * 100, 2)
            } for progress, mto_item in items]
            return pd.DataFrame(data)
        finally:
            session.close()

    # ------------------------------------------------------
    # عملیات مدیریتی پروژه
    # ------------------------------------------------------

    def rename_project(self, project_id: int, new_name: str) -> bool:
        session = self.session_factory()
        try:
            project = session.get(Project, project_id)
            if not project:
                return False
            project.name = new_name
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در تغییر نام پروژه {project_id}: {e}")
            return False
        finally:
            session.close()

    def copy_line_to_project(self, src_project_id: int, dest_project_id: int, line_no: str) -> bool:
        """
        کپی MTOProgress (و داده‌های مرتبط) یک خط به پروژه مقصد.
        """
        session = self.session_factory()
        try:
            items = session.query(MTOProgress).filter(
                MTOProgress.project_id == src_project_id,
                MTOProgress.line_no == line_no
            ).all()
            for i in items:
                new_item = MTOProgress(
                    project_id=dest_project_id,
                    line_no=line_no,
                    mto_item_id=i.mto_item_id,
                    item_code=i.item_code,
                    description=i.description,
                    unit=i.unit,
                    total_qty=i.total_qty,
                    used_qty=i.used_qty,
                    remaining_qty=i.remaining_qty
                )
                session.add(new_item)
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در کپی خط {line_no}: {e}")
            return False
        finally:
            session.close()

    def check_duplicates_in_project(self, project_id: int) -> List[str]:
        """
        شناسایی خطوط تکراری بر اساس MTOItem.
        """
        session = self.session_factory()
        try:
            duplicates = session.query(MTOItem.line_no).filter(
                MTOItem.project_id == project_id
            ).group_by(MTOItem.line_no).having(func.count(MTOItem.id) > 1).all()
            return [d[0] for d in duplicates]
        finally:
            session.close()

    def initialize_mto_progress_for_line(self, project_id: int, line_no: str) -> None:
        """
        ایجاد رکوردهای MTOProgress برای هر آیتم خط که وجود ندارد.
        """
        session = self.session_factory()
        try:
            items = session.query(MTOItem).filter(
                MTOItem.project_id == project_id,
                MTOItem.line_no == line_no
            ).all()
            for item in items:
                exists = session.query(MTOProgress.id).filter_by(
                    project_id=project_id,
                    line_no=line_no,
                    mto_item_id=item.id
                ).first()
                if not exists:
                    session.add(MTOProgress(
                        project_id=project_id,
                        line_no=line_no,
                        mto_item_id=item.id,
                        item_code=item.item_code,
                        description=item.description,
                        unit=item.unit,
                        total_qty=item.quantity,
                        used_qty=0,
                        remaining_qty=item.quantity
                    ))
            session.commit()
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در مقداردهی اولیه MTOProgress برای خط {line_no}: {e}")
        finally:
            session.close()

    def update_mto_progress(self, project_id: int, line_no: str, mto_item_id: int, used_qty: float) -> None:
        """
        بروزرسانی مقدار مصرف شده برای یک آیتم MTO در MTOProgress.
        """
        session = self.session_factory()
        try:
            progress = session.query(MTOProgress).filter_by(
                project_id=project_id, line_no=line_no, mto_item_id=mto_item_id
            ).first()
            if progress:
                progress.used_qty = used_qty
                progress.remaining_qty = (progress.total_qty or 0) - used_qty
                progress.last_updated = datetime.utcnow()
                session.commit()
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در بروزرسانی MTOProgress: {e}")
        finally:
            session.close()

    def get_used_qty(self, project_id: int, mto_item_id: int) -> float:
        """
        گرفتن مقدار مصرف شده برای یک آیتم MTO خاص.
        """
        session = self.session_factory()
        try:
            entry = session.query(MTOProgress.used_qty).filter_by(
                project_id=project_id, mto_item_id=mto_item_id
            ).first()
            return entry[0] if entry else 0
        finally:
            session.close()

    def suggest_line_no(self, project_id: int, typed_text: str, top_n: int = 15) -> List[str]:
        """
        پیشنهاد شماره خط برای پروژه.
        """
        if not typed_text or len(typed_text) < 2:
            return []
        session = self.session_factory()
        try:
            search_term = f"%{typed_text}%"
            results = session.query(MTOItem.line_no).filter(
                MTOItem.project_id == project_id,
                MTOItem.line_no.ilike(search_term)
            ).distinct().limit(top_n).all()
            return [r[0] for r in results]
        finally:
            session.close()

    def project_exists(self, project_name: str) -> bool:
        session = self.session_factory()
        try:
            return session.query(Project.id).filter(Project.name == project_name).first() is not None
        finally:
            session.close()

    def get_mto_progress_for_line(self, project_id: int, line_no: str) -> List[Dict[str, Any]]:
        """
        واکشی لیست MTOProgress برای یک خط مشخص به صورت دیکشنری.
        """
        session = self.session_factory()
        try:
            # Join با MTOItem برای گرفتن اطلاعات کامل
            items = session.query(MTOProgress, MTOItem).join(
                MTOItem, MTOProgress.mto_item_id == MTOItem.id
            ).filter(
                MTOProgress.project_id == project_id,
                MTOProgress.line_no == line_no
            ).all()

            result = []
            for progress, mto_item in items:
                # تبدیل به دیکشنری با فیلدهای مورد انتظار UI
                item_dict = {
                    'id': progress.id,
                    'project_id': progress.project_id,
                    'line_no': progress.line_no,
                    'mto_item_id': progress.mto_item_id,
                    'item_code': progress.item_code or mto_item.item_code,
                    'description': progress.description or mto_item.description,
                    'unit': progress.unit or mto_item.unit,
                    'mto_qty': progress.total_qty or 0,  # کلید مورد انتظار برای کل
                    'consumed_qty': progress.used_qty or 0,  # کلید مورد انتظار برای مصرف شده
                    'used_qty': progress.used_qty or 0,  # برای سازگاری
                    'remaining_qty': progress.remaining_qty or ((progress.total_qty or 0) - (progress.used_qty or 0)),
                    'last_updated': progress.last_updated,
                    # فیلدهای اضافی از MTOItem
                    'item_type': mto_item.item_type,
                    'p1_bore_in': mto_item.p1_bore_in,
                    # محاسبه درصد پیشرفت
                    'progress': round((progress.used_qty or 0) / (progress.total_qty or 1) * 100, 2)
                }
                result.append(item_dict)

            return result
        except Exception as e:
            logging.error(f"خطا در واکشی MTOProgress برای خط {line_no}: {e}")
            return []
        finally:
            session.close()

    # متدهای اضافی از DataManager اصلی

    def rebuild_mto_progress_for_line(self, project_id: int, line_no: str) -> None:
        """
        بازسازی MTOProgress برای یک خط بر اساس MTOConsumption records.
        """
        from models import MTOConsumption
        session = self.session_factory()
        try:
            # ابتدا MTOProgress را مقداردهی اولیه کن
            self.initialize_mto_progress_for_line(project_id, line_no)

            # سپس مصرف‌ها را محاسبه کن
            consumptions = session.query(
                MTOConsumption.mto_item_id,
                func.sum(MTOConsumption.used_qty).label('total_used')
            ).join(
                MTOItem, MTOConsumption.mto_item_id == MTOItem.id
            ).filter(
                MTOItem.project_id == project_id,
                MTOItem.line_no == line_no
            ).group_by(MTOConsumption.mto_item_id).all()

            for mto_item_id, total_used in consumptions:
                progress = session.query(MTOProgress).filter_by(
                    project_id=project_id,
                    line_no=line_no,
                    mto_item_id=mto_item_id
                ).first()
                if progress:
                    progress.used_qty = total_used or 0
                    progress.remaining_qty = (progress.total_qty or 0) - (total_used or 0)
                    progress.last_updated = datetime.utcnow()

            session.commit()
        except Exception as e:
            session.rollback()
            logging.error(f"خطا در بازسازی MTOProgress: {e}")
        finally:
            session.close()
