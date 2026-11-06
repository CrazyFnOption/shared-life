export interface User { id: string; username?:string; display_name: string; last_synced_at?: string | null; relation_epoch?:number }
export interface Family { id:string; name:string; owner_id:string; members:User[]; revision?:number; relation_epoch?:number }
export type ScopeRef = {scope:'private';family_id:null}|{scope:'shared';family_id:string}
export type Source = ScopeRef & {protocol_version:2;user_id:string;restore_id:string;relation_epoch:number}
export interface LegacySource {protocol_version:1;user_id:string;restore_id:string;scope:Scope;family_id:string|null;membership_version:number}
export interface AccountContext {protocol_version:2;restore_id:string;restored_at?:string|null;context_version:number;families:Family[];default_scope:ScopeRef&{version:number};default_invalidated_family_id:string|null;legacy_context_mappings:{family_id:string;membership_version:number;relation_epoch:number}[]}
export interface AccountSource {user_id:string;restore_id:string}
export interface FamilyContext { family:Family|null; membership_version:number; source?:Source; legacy_source?:LegacySource }
export interface FamilyInvite { id:string;family_id:string;family_name:string;inviter_id:string;inviter_name:string;target_user_id:string;target_username:string;status:string;created_at:string;expires_at:string }
export type Scope = 'shared' | 'private'
export interface ReviewData { rating: number | null; rating_reason: string }
export interface ExpenseData { amount_cents: number; purpose: string; occurred_at: string; location: string; category?: string; rating?: number | null; rating_reason?: string }
export interface Refund { id: string; amount_cents: number; occurred_at: string; reason: string; voided: boolean; version: number }
export interface Expense extends ExpenseData { id: string; owner_id: string; scope: Scope; family_id?:string|null; version: number; voided?: boolean; refunds?: Refund[]; net_amount_cents?: number }
export interface RefundData { refund_id: string; amount_cents: number; occurred_at: string; reason: string }
export type Maintenance = {kind:'update';data:ExpenseData}|{kind:'refund_create'|'refund_update';data:RefundData}|{kind:'refund_void';data:{refund_id:string}}|{kind:'review';data:ReviewData}|{kind:'scope';data:{scope:Scope}}|{kind:'void';data:Record<string,never>}
export type Proposal = Exclude<Maintenance,{kind:'review'|'scope'}>
export type Operation = ({operation_id:string;expense_id:string;base_version:number}&Maintenance)|{operation_id:string;expense_id:string;kind:'create';base_version:0;data:ExpenseData&{scope:Scope}}
export interface ChangeRequest { id:string; expense_id:string; requester_id:string; owner_id:string; base_version:number; kind:Proposal['kind']; data:Proposal['data']; reason:string; rejection_reason?:string; status:'pending'|'accepted'|'rejected'|'stale'; created_at:string; decided_at:string|null; before:Expense }
export interface RequestInput { id:string;expense_id:string;base_version:number;kind:Proposal['kind'];data:Proposal['data'];reason:string }
export interface Revision { version:number;operation_id:string;before:Expense|null;after:Expense }
export interface Pending { operation: Operation; source?:Source; legacy_source?:LegacySource; family_id?:string|null; membership_version?:number; frozen?:boolean; error?: string; error_code?:string; conflict?: Expense; restore_review?: boolean }
export interface Category { scope: Scope; name: string }
export interface PendingCategory extends Category { source?:Source;legacy_source?:LegacySource;family_id?:string|null; membership_version?:number; frozen?:boolean; error?: string; restore_review?: boolean }
export type CategoryChangeKind = 'rename' | 'delete' | 'reclassify'
export interface CategoryChangeInput { kind:CategoryChangeKind; scope:Scope; source_name?:string; target_name?:string; merge?:boolean; expense_ids?:string[] }
export interface CategoryApproval { owner_id:string; status:'pending'|'accepted'|'rejected'; decided_at:string|null; count:number }
export interface CategoryChange { preview_id:string; id:string|null; requester_id:string; scope:Scope; kind:CategoryChangeKind; merge?:boolean; source_name:string|null; target_name:string; reason:string; rejection_reason?:string; status:'preview'|'pending'|'applied'|'rejected'|'stale'; created_at:string; submitted_at:string|null; decided_at:string|null; total_count:number; own_count:number; other_owners:{owner_id:string;display_name:string;count:number}[]; approvals:CategoryApproval[]; items:{id:string;owner_id:string;base_version:number;category:string}[] }
export interface ImageMeta { id: string; expense_id: string; mime_type: string; size_bytes: number }
export interface LocalImage extends ImageMeta { source?:Source;original_source?:Source;destination_source?:Source;parent_scope_operation_id?:string;legacy_source?:LegacySource;blob?: Blob; pending?: 'upload' | 'delete'; family_id?:string|null; membership_version?:number; frozen?:boolean; error?: string; restore_review?: boolean; recovery_scope?:Scope }
export interface AttachmentChanges { add: {id:string;blob:Blob}[]; remove: string[] }
export interface RestoreState { restore_id?: string; restored_at?: string | null }
export interface Snapshot extends RestoreState,Partial<FamilyContext> { source?:Source;context_version?:number;user: User; users: User[]; expenses: Expense[]; synced_at: string; legacy_family_id?:string|null; categories?: Category[]; images?: ImageMeta[]; requests?:ChangeRequest[]; category_changes?:CategoryChange[]; withdrawn_request_ids?:string[] }
export interface Account extends RestoreState,Partial<FamilyContext> { memo_pending?:{active:number;frozen:number};plan_pending?:{active:number;frozen:number}; context?:AccountContext; source?:Source;legacy_source?:LegacySource;migration_version?:number;legacy_staging?:boolean;pending_summary?:{active:number;frozen:number};user: User; username: string; legacy_family_id?:string|null; snapshot: Expense[]; local: Expense[]; queue: Pending[]; users: User[]; synced_at: string | null; categories: Category[]; categoryQueue: PendingCategory[]; images: LocalImage[]; requests?:ChangeRequest[]; category_changes?:CategoryChange[]; withdrawn_request_ids?:string[] }
export interface LedgerPartition {key:string;user_id:string;source?:Source;legacy_source?:LegacySource;snapshot:Expense[];local:Expense[];users:User[];synced_at:string|null;categories:Category[];categoryQueue:PendingCategory[];images:LocalImage[];requests:ChangeRequest[];category_changes:CategoryChange[];withdrawn_request_ids:string[]}
export interface Receipt { operation_id: string; expense: Expense }

export interface AppSettings { monthly:{enabled:boolean;time:string;updated_by:string|null}; weekly:{enabled:boolean;weekday:number;time:string}; automation_paused:boolean; provider_configured:boolean; push_available:boolean; vapid_public_key:string|null;push_subscription_state?:'none'|'active'|'expired';notification_pending?:number;notification_uncertain?:number }
export type SettingsInput = { monthly?:{enabled:boolean;time:string}; weekly?:{enabled:boolean;weekday:number;time:string} }
export interface Report { id:string;scope:Scope;owner_id:string|null;month:string;status:'pending'|'running'|'ready'|'stale'|'failed'|'unconfigured';cost_status?:'reserved'|'settled'|'unknown'|null;data_version:string;generated_at:string|null;provider:string|null;model:string|null;error:string|null;summary:{total_cents:number;by_payer:{user_id:string;amount_cents:number}[];unrated_count:number};content:{items:{expense_id:string;analysis:string}[];summary:string}|null;expenses?:Expense[] }
