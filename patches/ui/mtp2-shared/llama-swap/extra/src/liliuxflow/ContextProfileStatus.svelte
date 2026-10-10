<!-- SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import {onToggleLoad} from '../stores/modelLoad';
  import {onMount} from 'svelte';
  import {uiText} from './runtime';
  import {sharedKeyHeaders} from './sharedKey.mjs';
  import {readProfileStatus} from './contextProfiles.mjs';

  type Profile = {model: string; context: number; enabled: boolean; loaded: boolean; state: string; queued: number; active?: number; pending?: number; cleanup?: number};
  let snapshot = $state<{profiles: readonly Profile[]; pending: number; paused: boolean; shared?: {state: string; residentContext: number; busy: boolean; active: number; pending: number; cleanup: number}} | null>(null);
  let unavailable = $state(false);
  function unloadShared(){const profile=snapshot?.profiles.find(row=>row.model.includes('-mtp2-'));if(profile&&snapshot?.shared&&!snapshot.shared.busy)onToggleLoad({id:profile.model,state:'ready',name:'',description:'',unlisted:false,peerID:''});}
  onMount(() => {
    let stopped = false;
    let running = false;
    let next: ReturnType<typeof setTimeout>;
    let request: AbortController | null = null;
    async function poll() {
      clearTimeout(next);
      if (stopped || running || document.hidden) return;
      running = true;
      request = new AbortController();
      const timeout = setTimeout(() => request?.abort(), 5000);
      try {
        const response = await fetch('/api/context-profiles', {method: 'GET', headers:sharedKeyHeaders(), credentials: 'same-origin', cache: 'no-store', signal: request.signal});
        const status = await readProfileStatus(response);
        if (!stopped) { snapshot = status; unavailable = false; }
      } catch { if (!stopped) { unavailable = true; snapshot = null; } }
      finally {
        clearTimeout(timeout); request = null; running = false;
        if (!stopped) next = setTimeout(poll, 2000);
      }
    }
    const visible = () => { if (!document.hidden) void poll(); };
    document.addEventListener('visibilitychange', visible);
    void poll();
    return () => { stopped = true; clearTimeout(next); request?.abort(); document.removeEventListener('visibilitychange', visible); };
  });
</script>

<section class="shrink-0 border-b px-4 py-2 text-xs" aria-label={$uiText('manager.context.title')} data-context-profile-status>
  <div class="mb-1 flex flex-wrap items-center gap-2">
    <strong>{$uiText('manager.context.title')}</strong>
    {#if unavailable}<span>{$uiText('manager.context.unavailable')}</span>
    {:else if !snapshot}<span>{$uiText('manager.context.refreshing')}</span>{/if}
    {#if snapshot?.paused}<span>{$uiText('manager.context.paused')}</span>{/if}
  </div>
  {#if snapshot}
    {#if snapshot.shared}
      <div class="mb-2 rounded-md border p-2" data-shared-engine>
        <strong>{$uiText('shared.context.title')}</strong> · {$uiText('manager.context.' + snapshot.shared.state)}
        <p>{$uiText('shared.context.oneModel')} {$uiText('shared.context.memoryUnavailable')}</p>
        <p>{$uiText('shared.context.resident', {count:snapshot.shared.residentContext.toLocaleString()})}</p>
        <p>{$uiText('shared.context.counts',snapshot.shared)}</p>
        {#if snapshot.shared.state==='ready'}<button class="rounded border px-2 py-1" disabled={snapshot.shared.busy} onclick={unloadShared}>{$uiText('shared.context.unload')}</button>{/if}
        {#if snapshot.shared.busy}<p>{$uiText('shared.context.busy')}</p>{/if}
      </div>
    {/if}
    <div class="flex flex-wrap gap-2">
      {#each snapshot.profiles as profile (profile.model)}
        <div class="flex min-w-0 flex-wrap items-center gap-1 rounded-md border px-2 py-1" data-profile-model={profile.model}>
          <code class="break-all">{profile.model}</code>
          <span>{profile.context.toLocaleString()} {$uiText('manager.context.tokens')}</span>
          <span>{$uiText('manager.context.' + profile.state)}</span>
          {#if profile.loaded && profile.state !== 'ready'}<span>{$uiText('manager.context.ready')}</span>{/if}
          {#if profile.active !== undefined}<span>{$uiText('shared.context.counts',profile)}</span>{/if}
          {#if profile.queued > 0}<span>{$uiText('manager.context.queuedCount', {count: profile.queued})}</span>{/if}
        </div>
      {/each}
    </div>
  {/if}
</section>
