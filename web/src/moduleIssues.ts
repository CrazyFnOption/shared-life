import type { Source } from './types'
export interface ModuleIssue {key:string;module:'memo'|'plan';entity_id:string;title:string;message:string;local_text:string;server_text:string;can_rebase:boolean;review:boolean;attachment:boolean;original_source?:Source}
export type ModuleIssueAction='discard'|'retry'|'rebase'|'review'
