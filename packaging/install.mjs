#!/usr/bin/env node
import { randomUUID } from 'node:crypto';
import { dispatchLifecycle } from './lifecycle.mjs';
const argv=process.argv.slice(2);const args=['setup','deepseek-harness',...argv];
try{const data=await dispatchLifecycle(args);process.stdout.write(JSON.stringify({apiVersion:'bugate.io/v2',kind:'CommandResult',operation:'setup',request_id:randomUUID(),invocation:'completed',data,errors:[]})+'\n');}catch(error){const code=error.exitCode??1;process.stdout.write(JSON.stringify({apiVersion:'bugate.io/v2',kind:'CommandResult',operation:'setup',request_id:randomUUID(),invocation:code===2?'invalid_input':'runtime_error',data:null,errors:[{code:error.code??'RUNTIME_FAILURE',message:error.message,details:error.details??null}]})+'\n');process.exitCode=code;}
