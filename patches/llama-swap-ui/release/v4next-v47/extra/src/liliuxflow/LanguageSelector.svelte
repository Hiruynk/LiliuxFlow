<script lang="ts">
 import {Languages} from "@lucide/svelte";
 import * as Menu from "$lib/components/ui/dropdown-menu/index.js";
 import {locale,uiText,chooseLocale} from "./runtime";
 import {AUTONYMS,LOCALES} from "./core.mjs";
</script>
<Menu.Root><Menu.Trigger class="flex w-full items-center gap-2 rounded-md px-2 py-2 text-sm hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-ring" aria-label={$uiText('locale.label')}><Languages class="size-4 shrink-0"/><span class="group-data-[collapsible=icon]:hidden whitespace-nowrap">{AUTONYMS[$locale as keyof typeof AUTONYMS]}</span></Menu.Trigger><Menu.Content><Menu.RadioGroup value={$locale} onValueChange={chooseLocale}>{#each LOCALES as value}<Menu.RadioItem {value}>{AUTONYMS[value as keyof typeof AUTONYMS]}</Menu.RadioItem>{/each}</Menu.RadioGroup></Menu.Content></Menu.Root>
