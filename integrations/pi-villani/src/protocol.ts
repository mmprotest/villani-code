export type BridgeEventType =
  | 'tool_started' | 'tool_finished' | 'command_started' | 'command_finished' | 'tool_result'
  | 'runner_heartbeat' | 'proxy_request_started' | 'proxy_request_completed' | 'proxy_request_failed'
  | 'model_request_started' | 'model_request_completed' | 'model_request_failed'
  | 'approval_required' | 'run_completed' | 'run_failed' | 'run_aborted'
  | 'run_started' | 'phase' | 'bridge_diagnostic' | 'stream_text' | 'error';
export interface BridgeEvent { type: BridgeEventType | string; [key: string]: any }
export interface OpenAIMessage { role: string; content?: any; tool_call_id?: string; tool_calls?: any[]; [key:string]: any }
export interface PiCompletionResult { text: string; tool_calls: any[] }
