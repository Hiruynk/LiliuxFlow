<script lang="ts">
 import {modelActionNotices,clearModelActionNotice} from "../stores/modelLoad";
 import {uiText} from "./runtime";
 import {Button} from "$lib/components/ui/button/index.js";
</script>
<div class="fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2">
{#each Object.entries($modelActionNotices) as [modelId,notice] (notice.id)}
 <div role="alert" class="rounded-lg border border-destructive bg-background p-3 text-sm text-foreground shadow-lg">
  <p>{notice.status===409?$uiText('model.busy'):$uiText('model.actionFailed',{id:notice.id})}</p>
  <Button variant="ghost" size="sm" onclick={()=>clearModelActionNotice(modelId)}>{$uiText('common.dismiss')}</Button>
 </div>
{/each}
</div>
