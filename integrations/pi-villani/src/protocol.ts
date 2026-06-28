export interface BridgeEvent { type: string; [key: string]: any }
export interface OpenAIMessage { role: string; content?: any; tool_call_id?: string; tool_calls?: any[]; [key:string]: any }
export interface PiCompletionResult { text: string; tool_calls: any[] }
