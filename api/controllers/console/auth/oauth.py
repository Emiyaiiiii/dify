import logging

import httpx
from flask import current_app, redirect, request
from flask_restx import Resource
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.exceptions import Unauthorized

from configs import dify_config
from constants.languages import languages
from events.tenant_event import tenant_was_created
from extensions.ext_database import db
from libs.datetime_utils import naive_utc_now
from libs.helper import extract_remote_ip
from libs.oauth import GitHubOAuth, CasdoorOAuth, OAuthUserInfo
from libs.token import (
    set_access_token_to_cookie,
    set_csrf_token_to_cookie,
    set_refresh_token_to_cookie,
)
from models import Account, AccountStatus
from services.account_service import AccountService, RegisterService, TenantService
from services.billing_service import BillingService
from services.errors.account import AccountNotFoundError, AccountRegisterError
from services.errors.workspace import WorkSpaceNotAllowedCreateError, WorkSpaceNotFoundError
from services.feature_service import FeatureService

from .. import console_ns

logger = logging.getLogger(__name__)


def get_oauth_providers():
    with current_app.app_context():
        if not dify_config.GITHUB_CLIENT_ID or not dify_config.GITHUB_CLIENT_SECRET:
            github_oauth = None
        else:
            github_oauth = GitHubOAuth(
                client_id=dify_config.GITHUB_CLIENT_ID,
                client_secret=dify_config.GITHUB_CLIENT_SECRET,
                redirect_uri=dify_config.CONSOLE_API_URL + "/console/api/oauth/authorize/github",
            )
        if not dify_config.CASDOOR_CLIENT_ID or not dify_config.CASDOOR_CLIENT_SECRET:
            casdoor_oauth = None
        else:
            casdoor_oauth = CasdoorOAuth(
                client_id=dify_config.CASDOOR_CLIENT_ID,
                client_secret=dify_config.CASDOOR_CLIENT_SECRET,
                redirect_uri=dify_config.CONSOLE_API_URL + "/console/api/oauth/authorize/casdoor",
            )

        OAUTH_PROVIDERS = {"github": github_oauth, "casdoor": casdoor_oauth}
        return OAUTH_PROVIDERS


@console_ns.route("/oauth/login/<provider>")
class OAuthLogin(Resource):
    @console_ns.doc("oauth_login")
    @console_ns.doc(description="Initiate OAuth login process")
    @console_ns.doc(
        params={"provider": "OAuth provider name (github/casdoor)", "invite_token": "Optional invitation token"}
    )
    @console_ns.response(302, "Redirect to OAuth authorization URL")
    @console_ns.response(400, "Invalid provider")
    def get(self, provider: str):
        invite_token = request.args.get("invite_token") or None
        OAUTH_PROVIDERS = get_oauth_providers()
        with current_app.app_context():
            oauth_provider = OAUTH_PROVIDERS.get(provider)
        if not oauth_provider:
            return {"error": "Invalid provider"}, 400

        auth_url = oauth_provider.get_authorization_url(invite_token=invite_token)
        return redirect(auth_url)


@console_ns.route("/oauth/authorize/<provider>")
class OAuthCallback(Resource):
    @console_ns.doc("oauth_callback")
    @console_ns.doc(description="Handle OAuth callback and complete login process")
    @console_ns.doc(
        params={
            "provider": "OAuth provider name (github/casdoor)",
            "code": "Authorization code from OAuth provider",
            "state": "Optional state parameter (used for invite token)",
        }
    )
    @console_ns.response(302, "Redirect to console with access token")
    @console_ns.response(400, "OAuth process failed")
    def get(self, provider: str):
        OAUTH_PROVIDERS = get_oauth_providers()
        with current_app.app_context():
            oauth_provider = OAUTH_PROVIDERS.get(provider)
        if not oauth_provider:
            return {"error": "Invalid provider"}, 400

        code = request.args.get("code")
        state = request.args.get("state")
        invite_token = None
        if state:
            invite_token = state

        if not code:
            return {"error": "Authorization code is required"}, 400

        try:
            token = oauth_provider.get_access_token(code)
            user_info = oauth_provider.get_user_info(token)
        except httpx.RequestError as e:
            error_text = str(e)
            if isinstance(e, httpx.HTTPStatusError):
                error_text = e.response.text
            logger.exception("An error occurred during the OAuth process with %s: %s", provider, error_text)
            return {"error": "OAuth process failed"}, 400

        if invite_token and RegisterService.is_valid_invite_token(invite_token):
            invitation = RegisterService.get_invitation_by_token(token=invite_token)
            if invitation:
                invitation_email = invitation.get("email", None)
                if invitation_email != user_info.email:
                    return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message=Invalid invitation token.")

            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin/invite-settings?invite_token={invite_token}")

        try:
            account = _generate_account(provider, user_info)
        except AccountNotFoundError:
            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message=Account not found.")
        except (WorkSpaceNotFoundError, WorkSpaceNotAllowedCreateError):
            return redirect(
                f"{dify_config.CONSOLE_WEB_URL}/signin"
                "?message=Workspace not found, please contact system admin to invite you to join in a workspace."
            )
        except AccountRegisterError as e:
            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message={e.description}")

        # Check account status
        if account.status == AccountStatus.BANNED:
            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message=Account is banned.")

        if account.status == AccountStatus.PENDING:
            account.status = AccountStatus.ACTIVE
            account.initialized_at = naive_utc_now()
            db.session.commit()

        try:
            TenantService.create_owner_tenant_if_not_exist(account)
        except Unauthorized:
            return redirect(f"{dify_config.CONSOLE_WEB_URL}/signin?message=Workspace not found.")
        except WorkSpaceNotAllowedCreateError:
            return redirect(
                f"{dify_config.CONSOLE_WEB_URL}/signin"
                "?message=Workspace not found, please contact system admin to invite you to join in a workspace."
            )

        token_pair = AccountService.login(
            account=account,
            ip_address=extract_remote_ip(request),
        )

        response = redirect(f"{dify_config.CONSOLE_WEB_URL}")

        set_access_token_to_cookie(request, response, token_pair.access_token)
        set_refresh_token_to_cookie(request, response, token_pair.refresh_token)
        set_csrf_token_to_cookie(request, response, token_pair.csrf_token)
        return response


def _get_account_by_openid_or_email(provider: str, user_info: OAuthUserInfo) -> Account | None:
    account: Account | None = Account.get_by_openid(provider, user_info.id)

    if not account:
        with Session(db.engine) as session:
            account = session.execute(select(Account).filter_by(email=user_info.email)).scalar_one_or_none()

    return account


def _sync_user_organizations(account: Account, user_info: OAuthUserInfo):
    """同步用户的Casdoor组织到Dify工作空间"""
    from models.account import CasdoorOrganizationMapping, Tenant
    
    # 仅处理Casdoor OAuth
    if not user_info.organizations:
        logging.info("No organizations to sync")
        return
    
    # 获取现有组织映射
    existing_mappings = db.session.query(CasdoorOrganizationMapping).all()
    org_to_tenant = {mapping.casdoor_org_id: mapping.tenant_id for mapping in existing_mappings}
    tenant_to_org = {mapping.tenant_id: mapping.casdoor_org_id for mapping in existing_mappings}
    
    # 获取用户当前关联的工作空间
    user_tenants = TenantService.get_join_tenants(account)
    user_tenant_ids = [tenant.id for tenant in user_tenants]
    
    # 处理每个组织
    for org in user_info.organizations:
        if not org:
            continue
        
        # 处理字符串类型的组织（来自Casdoor）
        if isinstance(org, str):
            # 解析组织字符串，格式为 company/org-name-children
            if '/' in org:
                # 分割根组织和子组织部分
                root_org, child_org_part = org.split('/', 1)
                
                # 解析完整的组织路径，处理通过"-"分隔的层级
                org_path_parts = child_org_part.split('-')
                
                # 处理根组织（Yrec）
                root_casdoor_org_id = f"{root_org}"
                if root_casdoor_org_id not in org_to_tenant:
                    # 创建根工作空间
                    root_tenant = Tenant(
                        name=root_org,
                        casdoor_org_id=root_casdoor_org_id,
                        parent_id=None,
                        plan="basic",
                        status="normal"
                    )
                    db.session.add(root_tenant)
                    db.session.flush()
                    
                    # 创建映射关系
                    root_mapping = CasdoorOrganizationMapping(
                        casdoor_org_id=root_casdoor_org_id,
                        tenant_id=root_tenant.id
                    )
                    db.session.add(root_mapping)
                    
                    # 更新映射关系
                    org_to_tenant[root_casdoor_org_id] = root_tenant.id
                    tenant_to_org[root_tenant.id] = root_casdoor_org_id
                
                current_parent_tenant_id = org_to_tenant[root_casdoor_org_id]
                
                # 保存所有层级的工作空间ID，用于后续添加用户
                all_level_tenant_ids = [org_to_tenant[root_casdoor_org_id]]
                
                # 处理中间层级
                for i, part in enumerate(org_path_parts):
                    # 构建当前层级的完整路径和名称
                    current_org_name = part
                    full_org_path = f"{root_org}/{'-'.join(org_path_parts[:i+1])}"
                    
                    # 创建当前层级的工作空间
                    if full_org_path not in org_to_tenant:
                        new_tenant = Tenant(
                            name=current_org_name,
                            casdoor_org_id=full_org_path,
                            parent_id=current_parent_tenant_id,
                            plan="basic",
                            status="normal"
                        )
                        db.session.add(new_tenant)
                        db.session.flush()
                        
                        # 创建映射关系
                        mapping = CasdoorOrganizationMapping(
                            casdoor_org_id=full_org_path,
                            tenant_id=new_tenant.id
                        )
                        db.session.add(mapping)
                        
                        # 更新映射关系
                        org_to_tenant[full_org_path] = new_tenant.id
                        tenant_to_org[new_tenant.id] = full_org_path
                    
                    # 添加当前层级的工作空间ID到列表
                    current_tenant_id = org_to_tenant[full_org_path]
                    all_level_tenant_ids.append(current_tenant_id)
                    
                    # 更新当前父级信息，用于下一层级
                    current_parent_tenant_id = current_tenant_id
                
                # 确保用户关联到所有层级的工作空间
                for tenant_id in all_level_tenant_ids:
                    if tenant_id not in user_tenant_ids:
                        tenant = db.session.query(Tenant).filter_by(id=tenant_id).first()
                        if tenant:
                            TenantService.create_tenant_member(tenant, account, role="owner")
                            user_tenant_ids.append(tenant_id)
            else:
                # 处理没有"/"的情况（简单组织）
                casdoor_org_id = org
                org_name = org
                
                if casdoor_org_id not in org_to_tenant:
                    # 创建新工作空间
                    new_tenant = Tenant(
                        name=org_name,
                        casdoor_org_id=casdoor_org_id,
                        parent_id=None,
                        plan="basic",
                        status="normal"
                    )
                    db.session.add(new_tenant)
                    db.session.flush()
                    
                    # 创建映射关系
                    mapping = CasdoorOrganizationMapping(
                        casdoor_org_id=casdoor_org_id,
                        tenant_id=new_tenant.id
                    )
                    db.session.add(mapping)
                    
                    # 更新映射关系
                    org_to_tenant[casdoor_org_id] = new_tenant.id
                    tenant_to_org[new_tenant.id] = casdoor_org_id
                
                # 确保用户关联到工作空间
                tenant_id = org_to_tenant[casdoor_org_id]
                if tenant_id not in user_tenant_ids:
                    tenant = db.session.query(Tenant).filter_by(id=tenant_id).first()
                    if tenant:
                        TenantService.create_tenant_member(tenant, account, role="owner")
                        user_tenant_ids.append(tenant_id)

    # 如果用户没有当前工作空间，设置第一个组织作为当前工作空间
    if not account.current_tenant and user_tenant_ids:
        first_tenant = db.session.query(Tenant).filter_by(id=user_tenant_ids[0]).first()
        if first_tenant:
            account.current_tenant = first_tenant
    
    db.session.commit()


def _generate_account(provider: str, user_info: OAuthUserInfo):
    # Get account by openid or email.
    account = _get_account_by_openid_or_email(provider, user_info)

    if account:
        tenants = TenantService.get_join_tenants(account)
        if not tenants:
            if not FeatureService.get_system_features().is_allow_create_workspace:
                raise WorkSpaceNotAllowedCreateError()
            else:
                new_tenant = TenantService.create_tenant(f"{account.name}'s Workspace")
                TenantService.create_tenant_member(new_tenant, account, role="owner")
                account.current_tenant = new_tenant
                tenant_was_created.send(new_tenant)

    if not account:
        if not FeatureService.get_system_features().is_allow_register:
            if dify_config.BILLING_ENABLED and BillingService.is_email_in_freeze(user_info.email):
                raise AccountRegisterError(
                    description=(
                        "This email account has been deleted within the past "
                        "30 days and is temporarily unavailable for new account registration"
                    )
                )
            else:
                raise AccountRegisterError(description=("Invalid email or password"))
        account_name = user_info.name or "Dify"
        account = RegisterService.register(
            email=user_info.email, name=account_name, password=None, open_id=user_info.id, provider=provider
        )

        # Set interface language
        preferred_lang = request.accept_languages.best_match(languages)
        if preferred_lang and preferred_lang in languages:
            interface_language = preferred_lang
        else:
            interface_language = languages[0]
        account.interface_language = interface_language
        db.session.commit()

    # Link account
    AccountService.link_account_integrate(provider, user_info.id, account)
    
    # Sync user organizations from Casdoor
    _sync_user_organizations(account, user_info)

    return account
