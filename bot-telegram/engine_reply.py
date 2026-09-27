"""Envoi des reponses auto: lu -> pause -> typing -> send."""
import asyncio, logging, random
from style import smash_style, HANDOFF_FALLBACKS, HumanHandoffRequired, DailyLimitReached
from tease import client_wants_teaser, tease_delivered, pick_teaser
logger = logging.getLogger(__name__)

class AutoReplyMixin:
    async def auto_reply(self, chat_id, revision, epoch):
        delivery_uncertain = False
        try:
            if self.mark_read:
                try:
                    await self.mark_read(chat_id)
                    print('Telegram : message marque lu avant reponse.', flush=True)
                except Exception as error:
                    logger.exception('Lecture Telegram ; conversation %s : %s', chat_id, type(error).__name__)
            wait = random.uniform(self.delay_min, self.delay_max)
            print(f'Reponse automatique : lecture {wait:.1f} s.', flush=True)
            await asyncio.sleep(wait)
            if not self.valid(chat_id, revision, epoch):
                return
            handoff = None
            try:
                reply = await self.automatic_draft(chat_id, revision, epoch)
            except HumanHandoffRequired as error:
                handoff = str(error)
                reply = smash_style(random.choice(HANDOFF_FALLBACKS))
            if reply is None or not self.valid(chat_id, revision, epoch):
                return
            async with self.lock(chat_id):
                if not self.valid(chat_id, revision, epoch):
                    return
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
                    if chat.get('tease_sent') and not tease_delivered(self.store, chat_id):
                        self.store.clear_tease_sent(chat_id)
                        chat = self.store.chat(chat_id) or {}
                    active = []
                    if self.get_active_teasers:
                        try:
                            active = list(self.get_active_teasers() or [])
                        except Exception as error:
                            logger.exception('Teasers ; conversation %s : %s', chat_id, type(error).__name__)
                    if latest_client and client_wants_teaser(self.store, chat_id, latest_client) and not chat.get('tease_sent') and active and self.send_media:
                        teaser = pick_teaser(active)
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
