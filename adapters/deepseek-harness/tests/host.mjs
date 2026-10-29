import { createRequire } from 'node:module';
const require = createRequire(process.env.BUGATE_DSH_MODULES ? process.env.BUGATE_DSH_MODULES + '/__bugate_runtime__.js' : new URL('../plugin/package.json', import.meta.url));
export const load = name => import(require.resolve(name));
export const { Context } = await load('@deepseek-ai/cordis');
export const { default: LlmRuntime, LlmAdapter, createUserMessage, ToolCallId } = await load('@deepseek-ai/dsh-llm');
export const { default: SessionStore, SessionId } = await load('@deepseek-ai/dsh-session');
export const { default: Projections } = await load('@deepseek-ai/dsh-session-projection');
export const { default: SystemPrompt } = await load('@deepseek-ai/dsh-system-prompt');
export const { default: Tools } = await load('@deepseek-ai/dsh-tools');
export const Native = await load('@deepseek-ai/dsh-tool-executor-native');
export const { default: Agents } = await load('@deepseek-ai/dsh-agent');
export const { default: AgentLoop } = await load('@deepseek-ai/dsh-agent-loop');
export const { default: FsLocal } = await load('@deepseek-ai/dsh-fs-local');
export const { default: SubprocessLocal } = await load('@deepseek-ai/dsh-subprocess-local');
export const { default: Skills } = await load('@deepseek-ai/dsh-skill');
export const { ToolExecutionPipeline } = await load('@deepseek-ai/dsh-tools/pipeline');
export function textResponse(text = 'deterministic fixture response') {
 return [{type:'block-start',index:0,blockType:'text'}, {type:'text-delta',index:0,text}, {type:'block-end',index:0,block:{type:'text',text}}, {type:'finish',reason:{kind:'stop'}}];
}
export function toolResponse(name,args,id='call-1') {
 const callId=ToolCallId(id), argumentsJson=JSON.stringify(args);
 return [{type:'block-start',index:0,blockType:'tool-call'}, {type:'tool-call-delta',index:0,id:callId,name,argumentsDelta:argumentsJson}, {type:'block-end',index:0,block:{type:'tool-call',id:callId,name,arguments:argumentsJson}}, {type:'finish',reason:{kind:'tool-calls'}}];
}
export class FixtureModel extends LlmAdapter {
 requests=[];
 constructor(script=[]){super();this.script=script;}
 async resolveModel(provider,model){return {provider,id:model,name:model,context:{contextWindow:100000}};}
 async *stream(options){this.requests.push(options);const response=this.script.shift() ?? textResponse();for(const frame of typeof response==='function'?response(options):response)yield frame;}
}
export async function host({model=new FixtureModel(),persistence,ptc=false,fs=FsLocal}={}) {
 const ctx=new Context();
 for(const plugin of [LlmRuntime,SessionStore,Projections,SystemPrompt,Tools,Agents,fs,SubprocessLocal,Skills])await ctx.plugin(plugin);
 const executor=ptc?undefined:await ctx.plugin(Native);
 await ctx.plugin((await load('@deepseek-ai/dsh-working-directory')).default);
 if(persistence)await ctx.plugin(await load('@deepseek-ai/dsh-session-persistence-jsonl'),{root:persistence,compression:'none'});
 await ctx.plugin(AgentLoop,{agents:[]});
 ctx.llm.registerAdapter(['fixture'],model);
 return {ctx,model,executor};
}
export async function send(agent,text='continue') {
 agent.followup(createUserMessage({content:[{type:'text',text}],source:{kind:'user'}}));
 await agent.whenIdle();
}
export function requestText(request){return request.messages.flatMap(m=>m.content).flatMap(b=>b.type==='text'?[b.text]:[]).join('\n');}
export function runtimeContexts(agent){return agent.session.snapshotEvents().filter(e=>e.type==='user/message'&&e.data.source.kind==='runtime-context');}
