import http, { type ServerResponse } from 'node:http';
import type { OpenAIMessage } from './protocol.js';

export type PiCompleteResolver = (name:string)=>Promise<any>;
export async function resolvePiComplete(importer:PiCompleteResolver=(name)=>import(name)):Promise<{fn:any;source:string}>{
  const candidates=['@earendil-works/pi-ai','@earendil-works/pi-ai/compat'];
  for(const name of candidates){
    try{
      const mod=await importer(name);
      const options:[string,any][]=[['complete',(mod as any).complete],['default.complete',(mod as any).default?.complete],['compat.complete',(mod as any).compat?.complete]];
      for(const [exportName,fn] of options) if(typeof fn==='function') return {fn,source:`${name}:${exportName}`};
    }catch{ /* try next candidate */ }
  }
  throw new Error('No compatible Pi completion helper found in @earendil-works/pi-ai or @earendil-works/pi-ai/compat.');
}

type Usage={input:number;output:number;totalTokens?:number};
type AssistantMessage={role?:'assistant';content?:any[];responseId?:string;timestamp:number;responseModel?:string;model?:string;usage?:Usage;stopReason?:'stop'|'toolUse'|'length'|'error'|'aborted'|string;errorMessage?:string};
export interface PiModelProxyOptions { model:any; apiKey?:string; headers?:Record<string,string>; signal?:AbortSignal; timeoutMs?:number; completeFn?:any; completeSource?:string; completeImporter?:PiCompleteResolver; pi?:any; }
export class UpstreamModelError extends Error{constructor(message:string){super(message);this.name='UpstreamModelError';}}
export class AbortRequestError extends Error{constructor(message='Pi model request aborted'){super(message);this.name='AbortRequestError';}}
export function abortError(){return new AbortRequestError();}
export function zeroUsage():Usage{return {input:0,output:0,totalTokens:0};}
export function sanitizeErrorMessage(e:unknown){return String((e as Error)?.message||e).replace(/Bearer\s+\S+/ig,'Bearer [redacted]').replace(/sk-[A-Za-z0-9_-]+/g,'[redacted]').replace(/(OPENAI_API_KEY|ANTHROPIC_API_KEY|VILLANI_API_KEY)=[^\s]+/ig,'$1=[redacted]').replace(/(authorization|api[-_]?key|x-api-key)["':= ]+[^,}\s]+/ig,'$1=[redacted]');}
export const sanitizeError=sanitizeErrorMessage;
function debug(message:string){if(process.env.VILLANI_PI_DEBUG==='1') console.error(`[pi-villani proxy] ${message}`);}
export function resolvePiModel(ctx:any):any{ if(process.env.VILLANI_USE_PI_MODEL==='false') return {id:process.env.VILLANI_MODEL||'villani-env-model', bypass:true}; const m=ctx?.model; if(!m) throw new Error('No active Pi model available. Select or launch Pi with a model, then retry.'); return m; }
function textOf(content:any):string{if(content==null)return ''; if(typeof content==='string')return content; if(Array.isArray(content))return content.map(p=>typeof p==='string'?p:(p?.text??p?.content??'')).join(''); return String(content);}
function timestampOf(m:any){return m?.timestamp??Date.now();}
function contentBlocksOf(m:any){const blocks:any[]=[]; const text=textOf(m.content); if(text) blocks.push({type:'text',text}); for(const c of m.tool_calls||[]) blocks.push({type:'toolCall',...fromOpenAIToolCall(c)}); return blocks;}
function openAIChatToPiContext(request:any){const messages:OpenAIMessage[]=request?.messages||[]; const ctx:any={messages:[],tools:toPiTools(request?.tools)}; const system:string[]=[]; for(const m of messages){if(m.role==='system'){system.push(textOf(m.content)); continue;} if(m.role==='user') ctx.messages.push({role:'user',content:textOf(m.content),timestamp:timestampOf(m)}); else if(m.role==='assistant'){const content=contentBlocksOf(m); ctx.messages.push({role:'assistant',content,api:'openai-completions',provider:'pi',model:m.model,usage:zeroUsage(),stopReason:content.some((block)=>block.type==='toolCall')?'toolUse':'stop',timestamp:timestampOf(m)});} else if(m.role==='tool') ctx.messages.push({role:'toolResult',toolCallId:m.tool_call_id,toolName:m.name??m.toolName,content:[{type:'text',text:textOf(m.content)}],isError:false,timestamp:timestampOf(m)});} if(system.length) ctx.systemPrompt=system.join('\n'); return ctx;}
function toPiContext(messages:OpenAIMessage[]=[], tools:any[]|undefined){return openAIChatToPiContext({messages,tools});}
function toPiTools(tools:any[]|undefined){return tools?.map(t=>t?.function?{name:t.function.name,description:t.function.description,parameters:t.function.parameters}:t);}
function fromOpenAIToolCall(c:any){return {id:c.id,name:c.name??c.function?.name,arguments:parseArgs(c.arguments??c.function?.arguments)};}
function parseArgs(a:any){if(typeof a==='string'){try{return JSON.parse(a);}catch{return a;}} return a??{};}
function normalizeToolArguments(value:unknown):Record<string,unknown>{ if(value==null) return {}; if(typeof value==='string'){ const trimmed=value.trim(); if(!trimmed) return {}; try{ const parsed=JSON.parse(trimmed); if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed)) return parsed as Record<string,unknown>; return {}; }catch{return {};} } if(typeof value==='object'&&!Array.isArray(value)) return value as Record<string,unknown>; return {}; }
function toOpenAIToolCalls(blocks:any[]){return blocks.filter((block)=>block?.type==='toolCall').map((block,index)=>{const rawArguments=block.arguments??block.function?.arguments; return {id:block.id||`call_${index}`,type:'function',function:{name:String(block.name??block.function?.name??''),arguments:JSON.stringify(normalizeToolArguments(rawArguments))}};});}
export function toOpenAIFinishReason(reason:AssistantMessage['stopReason']):string{if(reason==='toolUse')return 'tool_calls'; if(reason==='length')return 'length'; return 'stop';}
export function toOpenAIUsage(usage:Usage):Record<string,number>{return {prompt_tokens:usage.input,completion_tokens:usage.output,total_tokens:usage.totalTokens||usage.input+usage.output};}
export function piAssistantToOpenAIResponse(message:AssistantMessage):Record<string,unknown>{
  if(message.stopReason==='error') throw new UpstreamModelError(message.errorMessage??'Pi model request failed');
  if(message.stopReason==='aborted') throw abortError();
  const content=Array.isArray(message.content)?message.content:[];
  const text=content.filter((block)=>block?.type==='text').map((block)=>String(block.text??'')).join('\n\n');
  const toolCalls=toOpenAIToolCalls(content);
  const response={id:message.responseId??`pi-villani-${message.timestamp}`,object:'chat.completion',created:Math.floor(message.timestamp/1000),model:message.responseModel??message.model,choices:[{index:0,message:{role:'assistant',content:text||null,...(toolCalls.length?{tool_calls:toolCalls}:{})},finish_reason:toOpenAIFinishReason(message.stopReason)}],usage:toOpenAIUsage(message.usage??zeroUsage())};
  return response;
}
export function writeOpenAIStream(res:ServerResponse,message:AssistantMessage):void{const response=piAssistantToOpenAIResponse(message) as any; const choice=response.choices[0]; const fullMessage=choice.message; res.writeHead(200,{"content-type":"text/event-stream; charset=utf-8","cache-control":"no-cache",connection:"keep-alive"}); res.write(`data: ${JSON.stringify({id:response.id,object:"chat.completion.chunk",created:response.created,model:response.model,choices:[{index:0,delta:fullMessage,finish_reason:choice.finish_reason}],usage:response.usage})}\n\n`); res.write("data: [DONE]\n\n"); res.end();}
export function writeJson(res:ServerResponse,status:number,body:unknown){res.writeHead(status,{'content-type':'application/json'}); res.end(JSON.stringify(body));}
export function writeProxyError(res:ServerResponse,error:unknown){writeJson(res,500,{error:{message:sanitizeErrorMessage(error),type:'upstream_error',code:error instanceof AbortRequestError?'pi_completion_aborted':'pi_completion_failed'}});}
function normalizeAssistant(raw:any,payload:any,model:any):AssistantMessage{const now=Date.now(); if(Array.isArray(raw?.content)) return {...raw,timestamp:raw.timestamp??now,model:raw.model??payload.model??model?.id??'pi-current-model',usage:raw.usage??zeroUsage()}; const text=raw?.content??raw?.message?.content??raw?.text??''; return {...raw,content:text?[{type:'text',text:String(text)}]:[],timestamp:raw?.timestamp??now,model:raw?.model??payload.model??model?.id??'pi-current-model',usage:raw?.usage??zeroUsage()};}

export class PiModelProxy { private server?:http.Server; completionSource?:string; constructor(private readonly options:PiModelProxyOptions){this.completionSource=options.completeSource;}
  async start():Promise<string>{this.server=http.createServer((req,res)=>void this.handle(req,res)); if(this.options.signal) this.options.signal.addEventListener('abort',()=>void this.stop(),{once:true}); await new Promise<void>(r=>this.server!.listen(0,'127.0.0.1',r)); const addr=this.server.address(); if(!addr||typeof addr==='string') throw new Error('Proxy bind failed'); const url=`http://127.0.0.1:${addr.port}`; debug(`listening on ${url}`); return url;}
  async stop():Promise<void>{const s=this.server; if(!s)return; this.server=undefined; await new Promise<void>(r=>s.close(()=>r()));}
  private async handle(req:http.IncomingMessage,res:http.ServerResponse){if(req.method!=='POST'||(req.url||'').split('?')[0]!=='/v1/chat/completions'){res.writeHead(404).end();return;} debug('POST /v1/chat/completions'); try{const body=await new Promise<string>((resolve,reject)=>{let b=''; req.on('data',d=>b+=d); req.on('end',()=>resolve(b)); req.on('error',reject);}); const payload=JSON.parse(body||'{}'); const context=openAIChatToPiContext(payload); const assistant=normalizeAssistant(await this.complete(context,payload),payload,this.options.model); if(payload.stream) writeOpenAIStream(res,assistant); else writeJson(res,200,piAssistantToOpenAIResponse(assistant));}catch(e){debug(`Pi complete failed: ${sanitizeErrorMessage(e)}`); writeProxyError(res,e);}}
  private async complete(context:any,payload:any){
    debug('calling Pi complete');
    const opts={apiKey:this.options.apiKey,headers:this.options.headers,maxTokens:payload.max_tokens,temperature:payload.temperature,signal:this.options.signal,timeoutMs:this.options.timeoutMs};
    let completeFn=this.options.completeFn;
    if(typeof completeFn==='function'){
      this.completionSource=this.options.completeSource??'injected:completeFn';
      const r=await completeFn(this.options.model,context,opts); debug('Pi complete returned'); return r;
    }
    let resolveError:unknown;
    try{const resolved=await resolvePiComplete(this.options.completeImporter); completeFn=resolved.fn; this.completionSource=resolved.source;}catch(e){resolveError=e;}
    if(typeof completeFn==='function'){const r=await completeFn(this.options.model,context,opts); debug('Pi complete returned'); return r;}
    debug(`Pi completion helper unavailable: ${sanitizeErrorMessage(resolveError)}`);
    const model=this.options.model;
    const fallbacks:[string,any][]=[['model.api.streamSimple',model?.api?.streamSimple],['model.complete',model?.complete],['ctx.pi.complete',this.options.pi?.complete]];
    for(const [source,fn] of fallbacks){if(typeof fn!=='function') continue; this.completionSource=source; const r=await fn.call(source==='ctx.pi.complete'?this.options.pi:(source==='model.complete'?model:model?.api),model,context,opts); debug(`${source} returned`); return r;}
    throw new Error('Active Pi model cannot be proxied: no supported completion API found.');
  }
}
export async function startModelProxyFromPiModel(options:PiModelProxyOptions){const p=new PiModelProxy(options); const url=await p.start(); return {url,close:()=>p.stop(),proxy:p,get completionSource(){return p.completionSource;}};}
export const startModelProxy=startModelProxyFromPiModel;
export const _test={toPiContext,openAIChatToPiContext,piAssistantToOpenAIResponse,writeOpenAIStream,sanitizeErrorMessage,toOpenAIToolCalls,normalizeToolArguments};
