<script lang="ts">
  import { locale } from "./runtime";
  import { localizedRelativeTime } from "./timeDisplay";
  let { timestamp }: { timestamp: string } = $props();
  let text = $derived(localizedRelativeTime(timestamp, $locale));
</script>
<span>{text}</span>
