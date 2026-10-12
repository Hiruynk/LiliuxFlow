<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
 import {onMount} from 'svelte';
 import {uiText} from './runtime';
 import {setSharedKey} from './sharedKey.mjs';
 import {fetchPlaygroundModels} from '../stores/api';
 let value=$state('');let enabled=$state(false);
 onMount(()=>{let stopped=false;fetch('/api/context-profiles',{cache:'no-store'}).then(r=>r.ok?r.json():null).then(value=>{if(!stopped)enabled=Boolean(value?.shared_available||value?.profiles?.some((row:{model:string;enabled:boolean})=>row.enabled&&/^qwen3\.8-flash-next-lily-q4-mtp2-(64k|128k|262k)$/.test(row.model)));}).catch(()=>{});return()=>{stopped=true;};});
 function changed(){setSharedKey(value);void fetchPlaygroundModels();}
</script>
{#if enabled}
<div class="space-y-1 rounded-md border p-2 text-xs" data-shared-virtual-key>
 <label class="block">{$uiText('shared.context.virtualKey')}<input type="password" autocomplete="off" spellcheck="false" bind:value class="ml-2 rounded border bg-background p-1" onchange={changed} /></label>
 <p>{$uiText('shared.context.keyHint')}</p>
</div>

{/if}
