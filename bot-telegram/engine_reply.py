"""Envoi des reponses auto: lu -> court delai -> typing (pendant draft) -> send."""
import asyncio, logging, random
from style import smash_style, HANDOFF_FALLBACKS, HumanHandoffRequired, DailyLimitReached, pick_fallback
from tease import client_wants_teaser, teasers_sent_count, pick_teaser
logger = logging.getLogger(__name__)

class AutoReplyMixin:
    async def _typing_keepalive(self, chat_id, stop_event):
        """Affiche 'écrit…' rapidement et le maintient pendant la generation."""
        pulse = getattr(self, 'typing_pulse', None)
        cancel = getattr(self, 'cancel_typing', None)
        if not pulse:
            return
        try:
            while not stop_event.is_set():
                try:
                    await pulse(chat_id)
                except Exception as error:
                    logger.exception('Saisie Telegram ; conversation %s : %s', chat_id, type(error).__name__)
                    return
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    continue
        finally:
            if cancel:
                try:
                    await cancel(chat_id)
                except Exception:
                    pass

    async def auto_reply(self, chat_id, revision, epoch):
        delivery_uncertain = False
        typing_stop = asyncio.Event()
        typing_task = None
        try:
            # 1) Marquer lu TOT, avant tout draft long
            if self.mark_read:
                try:
                    await self.mark_read(chat_id)
                    print('Telegram : message marque lu avant reponse.', flush=True)
                except Exception as error:
                    logger.exception('Lecture Telegram ; conversation %s : %s', chat_id, type(error).__name__)
            # 2) Court delai humain avant d'afficher "écrit…"
            wait = random.uniform(self.delay_min, self.delay_max)
            print(f'Reponse automatique : pause avant saisie {wait:.1f} s.', flush=True)
            await asyncio.sleep(wait)
            if not self.valid(chat_id, revision, epoch):
                return
            # 3) Demarrer typing rapidement (1er pulse sync), puis generer en parallele
            if getattr(self, 'typing_pulse', None):
                try:
                    await self.typing_pulse(chat_id)
                except Exception as error:
                    logger.exception('Saisie Telegram ; conversation %s : %s', chat_id, type(error).__name__)
                typing_task = asyncio.create_task(self._typing_keepalive(chat_id, typing_stop))
            handoff = None
            try:
                reply = await self.automatic_draft(chat_id, revision, epoch)
            except HumanHandoffRequired as error:
                handoff = str(error)
                recent = [m['text'] for m in self.store.messages(chat_id, 8) if m.get('source') == 'ai']
                reply = smash_style(pick_fallback(recent, HANDOFF_FALLBACKS))
            finally:
                typing_stop.set()
                if typing_task:
                    try:
                        await asyncio.wait_for(typing_task, timeout=2.0)
                    except Exception:
                        typing_task.cancel()
                        try:
                            await typing_task
                        except Exception:
                            pass
            if reply is None or not self.valid(chat_id, revision, epoch):
                return
            async with self.lock(chat_id):
                if not self.valid(chat_id, revision, epoch):
                    return
                # Micro-typage post-draft (court) pour coller a la longueur, sans longue attente
                if self.simulate_typing:
                    try:
                        await self.simulate_typing(chat_id, reply)
                    except Exception as error:
                        logger.exception('Saisie Telegram ; conversation %s : %s', chat_id, type(error).__name__)
                if not self.valid(chat_id, revision, epoch):
                    return
                try:
                    latest_client = None
                    for m in reversed(self.store.messages(chat_id, 8)):
                        if m['source'] == 'client':
                            latest_client = m['text']
                            break
                    chat = self.store.chat(chat_id) or {}
                    sent = max(int(chat.get('tease_sent') or 0), teasers_sent_count(self.store, chat_id))
                    active = []
                    if self.get_active_teasers:
                        try:
                            active = list(self.get_active_teasers() or [])
                        except Exception as error:
                            logger.exception('Teasers ; conversation %s : %s', chat_id, type(error).__name__)
                    if latest_client and client_wants_teaser(self.store, chat_id, latest_client) and sent < len(active) and self.send_media:
                        teaser = pick_teaser(active, sent)
                        path = (teaser.get('path') or teaser.get('filepath')) if teaser else None
                        if path:
                            try:
                                message_id = await self.send_media(chat_id, path, caption=None)
                                self.store.add(chat_id, message_id, 'ai', '[Vidéo avant-goût]')
                                self.store.mark_tease_sent(chat_id)
                                print('Telegram : avant-gout video envoye.', flush=True)
                            except Exception as error:
                                logger.exception('Envoi avant-gout echoue ; conversation %s : %s', chat_id, type(error).__name__)
                    message_id = await self.transport(chat_id, reply)
                    self.store.add(chat_id, message_id, 'ai', reply)
                except Exception:
                    delivery_uncertain = True
                    self.report_error(chat_id, 'Envoi incertain.')
                    raise
                finally:
                    if handoff and 'encaisser' in handoff:
                        self.set_mode(chat_id, 'manual')
                        if not delivery_uncertain:
                            self.report_error(chat_id, 'Client pret a payer. Reprenez pour encaisser.')
                print('Telegram : reponse automatique envoyee.', flush=True)
                if not handoff:
                    self.errors.pop(chat_id, None)
        except asyncio.CancelledError:
            typing_stop.set()
            if typing_task:
                typing_task.cancel()
            raise
        except HumanHandoffRequired as error:
            self.report_error(chat_id, str(error))
        except DailyLimitReached:
            self.report_error(chat_id, 'Plafond quotidien IA atteint.')
        except Exception as error:
            if self.valid(chat_id, revision, epoch) and not delivery_uncertain:
                self.report_error(chat_id, 'Reponse automatique echouee (' + type(error).__name__ + ').')
            logger.exception('Reponse automatique ; conversation %s : %s', chat_id, type(error).__name__)

    async def outgoing(self, chat_id, message_id, text, created=None):
        async with self.lock(chat_id):
            if self.store.known(chat_id, message_id):
                return
            self.set_mode(chat_id, 'manual')
            self.store.add(chat_id, message_id, 'human', text, created)

    async def reply(self, chat_id, text, request_id):
        self.set_mode(chat_id, 'manual')
        async with self.lock(chat_id):
            previous = self.store.claim_request(request_id, chat_id, text)
            if previous:
                if previous['status'] == 'sent':
                    return previous['message_id']
                raise ValueError('Envoi precedent incertain.')
            message_id = await self.transport(chat_id, text)
            self.store.add(chat_id, message_id, 'human', text)
            self.store.finish_request(request_id, message_id)
            return message_id

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
