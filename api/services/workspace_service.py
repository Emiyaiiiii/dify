from flask_login import current_user

from configs import dify_config
from extensions.ext_database import db
from models.account import Tenant, TenantAccountJoin, TenantAccountRole
from services.account_service import TenantService
from services.feature_service import FeatureService


class WorkspaceService:
    @classmethod
    def get_tenant_info(cls, tenant: Tenant):
        if not tenant:
            return None
        tenant_info: dict[str, object] = {
            "id": tenant.id,
            "name": tenant.name,
            "plan": tenant.plan,
            "status": tenant.status,
            "created_at": tenant.created_at,
            "trial_end_reason": None,
            "role": "normal",
        }

        # Get role of user
        tenant_account_join = (
            db.session.query(TenantAccountJoin)
            .where(TenantAccountJoin.tenant_id == tenant.id, TenantAccountJoin.account_id == current_user.id)
            .first()
        )
        assert tenant_account_join is not None, "TenantAccountJoin not found"
        tenant_info["role"] = tenant_account_join.role

        can_replace_logo = FeatureService.get_features(tenant.id).can_replace_logo

        if can_replace_logo and TenantService.has_roles(tenant, [TenantAccountRole.OWNER, TenantAccountRole.ADMIN]):
            base_url = dify_config.FILES_URL
            replace_webapp_logo = (
                f"{base_url}/files/workspaces/{tenant.id}/webapp-logo"
                if tenant.custom_config_dict.get("replace_webapp_logo")
                else None
            )
            remove_webapp_brand = tenant.custom_config_dict.get("remove_webapp_brand", False)

            tenant_info["custom_config"] = {
                "remove_webapp_brand": remove_webapp_brand,
                "replace_webapp_logo": replace_webapp_logo,
            }

        return tenant_info
    
    @classmethod
    def get_tenant_hierarchy(cls, tenant_id: str) -> dict:
        """获取工作空间层级结构"""
        tenant = db.session.query(Tenant).filter_by(id=tenant_id).first()
        if not tenant:
            return {}
        
        # 递归获取子工作空间
        def get_children(tenant_id: str) -> list:
            children = db.session.query(Tenant).filter_by(parent_id=tenant_id).all()
            result = []
            for child in children:
                result.append({
                    "id": child.id,
                    "name": child.name,
                    "children": get_children(child.id)
                })
            return result
        
        return {
            "id": tenant.id,
            "name": tenant.name,
            "children": get_children(tenant.id)
        }
    
    @classmethod
    def get_all_tenants_with_hierarchy(cls) -> list:
        """获取所有工作空间的层级结构"""
        # 获取所有根工作空间
        root_tenants = db.session.query(Tenant).filter_by(parent_id=None).all()
        
        result = []
        for tenant in root_tenants:
            # 递归获取子工作空间
            def get_children(tenant_id: str) -> list:
                children = db.session.query(Tenant).filter_by(parent_id=tenant_id).all()
                child_list = []
                for child in children:
                    child_list.append({
                        "id": child.id,
                        "name": child.name,
                        "children": get_children(child.id)
                    })
                return child_list
            
            result.append({
                "id": tenant.id,
                "name": tenant.name,
                "children": get_children(tenant.id)
            })
        
        return result
