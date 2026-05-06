#  RSS to Telegram Bot
#  Copyright (C) 2021-2024  Rongrong <i@rong.moe>
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Affero General Public License as
#  published by the Free Software Foundation, either version 3 of the
#  License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU Affero General Public License for more details.
#
#  You should have received a copy of the GNU Affero General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.

from __future__ import annotations
from typing import Optional
import asyncio
import re

from .. import db, env
from ..errors_collection import MediaSendFailErrors
from .utils import parse_entry, logger, Enclosure
from .post_formatter import PostFormatter
from .message import MessageDispatcher


async def get_post_from_entry(entry, feed_title: str, feed_link: str = None) -> 'Post':
    entry_parsed = await parse_entry(entry, feed_link)
    return Post(
        html=entry_parsed.content,
        title=entry_parsed.title,
        feed_title=feed_title,
        link=entry_parsed.link,
        author=entry_parsed.author,
        tags=entry_parsed.tags,
        feed_link=feed_link,
        enclosures=entry_parsed.enclosures
    )


class Post:
    def __init__(self,
                 html: str,
                 title: Optional[str] = None,
                 feed_title: Optional[str] = None,
                 link: Optional[str] = None,
                 author: Optional[str] = None,
                 tags: Optional[list[str]] = None,
                 feed_link: Optional[str] = None,
                 enclosures: list[Enclosure] = None):
        """
        :param html: HTML content
        :param title: post title
        :param feed_title: feed title
        :param link: post link
        :param author: post author
        :param tags: post tags
        :param feed_link: the url of the feed where the post from
        """
        self.html = html
        self.title = title
        self.feed_title = feed_title
        self.link = link
        self.author = author
        self.tags = tags
        self.feed_link = feed_link
        self.enclosures = enclosures

        self.post_formatter = PostFormatter(
            html=self.html,
            title=self.title,
            feed_title=self.feed_title,
            link=self.link,
            author=self.author,
            tags=self.tags,
            feed_link=self.feed_link,
            enclosures=self.enclosures
        )

        # AI tags cache: None=not fetched yet, []=fetched but empty, [tag, ...]=fetched with results
        self._ai_tags: Optional[list[str]] = None
        self._ai_tags_lock: asyncio.Lock = asyncio.Lock()

    async def _get_ai_tags(self) -> list[str]:
        """Fetch AI-generated tags, caching the result for this Post instance."""
        if self._ai_tags is not None:
            return self._ai_tags
        async with self._ai_tags_lock:
            if self._ai_tags is not None:
                return self._ai_tags
            from ..ai.tagger import generate_tags
            plain = re.sub(r'<[^>]+>', ' ', self.html or '')
            plain = re.sub(r'\s+', ' ', plain).strip()
            self._ai_tags = await generate_tags(title=self.title or '', content=plain)
        return self._ai_tags

    async def send_formatted_post_according_to_sub(self, sub: db.Sub):
        if not isinstance(sub.feed, db.User):
            await sub.fetch_related('user')
        user: db.User = sub.user
        ai_tags_val = sub.ai_tags if sub.ai_tags != -100 else user.ai_tags
        enable_ai_tags = bool(
            env.OPENAI_API_KEY
            and (ai_tags_val == 1 or (ai_tags_val == 0 and env.AI_TAGGING_ENABLED))
        )
        await self.send_formatted_post(
            user_id=sub.user_id,
            sub_title=sub.title,
            tags=sub.tags.split(' ') if sub.tags else [],
            send_mode=sub.send_mode if sub.send_mode != -100 else user.send_mode,
            length_limit=sub.length_limit if sub.length_limit != -100 else user.length_limit,
            link_preview=sub.link_preview if sub.link_preview != -100 else user.link_preview,
            display_author=sub.display_author if sub.display_author != -100 else user.display_author,
            display_via=sub.display_via if sub.display_via != -100 else user.display_via,
            display_title=sub.display_title if sub.display_title != -100 else user.display_title,
            display_entry_tags=sub.display_entry_tags if sub.display_entry_tags != -100 else user.display_entry_tags,
            style=sub.style if sub.style != -100 else user.style,
            display_media=sub.display_media if sub.display_media != -100 else user.display_media,
            silent=not (sub.notify if sub.notify != -100 else user.notify),
            enable_ai_tags=enable_ai_tags,
        )

    async def send_formatted_post(self,
                                  user_id: int,
                                  sub_title: Optional[str] = None,
                                  tags: Optional[list[str]] = None,
                                  send_mode: int = 0,
                                  length_limit: int = 0,
                                  link_preview: int = 0,
                                  display_author: int = 0,
                                  display_via: int = 0,
                                  display_title: int = 0,
                                  display_entry_tags: int = -1,
                                  style: int = 0,
                                  display_media: int = 0,
                                  silent: bool = False,
                                  enable_ai_tags: bool = False):
        """
        Send formatted post.

        :param user_id: user id
        :param sub_title: Sub title, overriding feed title if set
        :param tags: Tags of the sub
        :param send_mode: -1=force link, 0=auto, 1=force Telegraph, 2=force message
        :param length_limit: Telegraph length limit, valid when send_mode==0. If exceeded, send via Telegraph; If is 0,
            send via Telegraph when a post cannot be sent in a single message
        :param link_preview: 0=auto, 1=force enable
        :param display_author: -1=disable, 0=auto, 1=force display
        :param display_via: -2=completely disable, -1=disable but display link, 0=auto, 1=force display
        :param display_title: -1=disable, 0=auto, 1=force display
        :param display_entry_tags: -1=disable, 1=force display
        :param style: 0=RSStT, 1=flowerss
        :param display_media: -1=disable, 0=enable
        :param silent: whether to send with notification sound
        :param enable_ai_tags: whether to append AI-generated hashtags to the post
        """
        ai_tag_str = ''
        if enable_ai_tags:
            ai_tags = await self._get_ai_tags()
            if ai_tags:
                ai_tag_str = '\n' + ' '.join(f'#{t}' for t in ai_tags)

        for _ in range(3):
            try:
                formatted_post_tuple = \
                    await self.post_formatter.get_formatted_post(sub_title=sub_title,
                                                                 tags=tags,
                                                                 send_mode=send_mode,
                                                                 length_limit=length_limit,
                                                                 link_preview=link_preview,
                                                                 display_author=display_author,
                                                                 display_via=display_via,
                                                                 display_title=display_title,
                                                                 display_entry_tags=display_entry_tags,
                                                                 style=style,
                                                                 display_media=display_media)

                if formatted_post_tuple is None:
                    logger.debug(f'Post {self.link} is not sent to user {user_id} due to empty content')
                    return  # skip

                formatted_post, need_media, need_link_preview = formatted_post_tuple

                if ai_tag_str:
                    formatted_post = formatted_post + ai_tag_str

                message_dispatcher = MessageDispatcher(user_id=user_id,
                                                       html=formatted_post,
                                                       media=self.post_formatter.media if need_media else None,
                                                       link_preview=need_link_preview,
                                                       silent=silent)

                return await message_dispatcher.send_messages()
            except MediaSendFailErrors as e:
                media = self.post_formatter.media
                log_header = f'Failed to send post to user {user_id} (feed: {self.feed_link}, post: {self.link}) ' \
                             f'due to {type(e).__name__}'
                msg_count_prev = await media.estimate_message_counts()
                if media.allow_mixing_images_and_videos:
                    media.allow_mixing_images_and_videos = False
                    msg_count_new = await media.estimate_message_counts()
                    if msg_count_new != msg_count_prev:
                        # the videos may not be able to mixed with images split them and try again
                        logger.debug(f'{log_header}, disallowed mixing images and videos and retrying')
                        continue
                if not media.consider_videos_as_gifs:
                    media.consider_videos_as_gifs = True
                    msg_count_new = await media.estimate_message_counts()
                    if msg_count_new != msg_count_prev:
                        logger.debug(f'{log_header}, let each video occupy a single message and retrying')
                        continue
                if media.allow_files_sent_as_album:
                    media.allow_files_sent_as_album = False
                    msg_count_new = await media.estimate_message_counts()
                    if msg_count_new != msg_count_prev:
                        logger.debug(f'{log_header}, disallowed files sent as album and retrying')
                        continue
                logger.error(f'{log_header}, dropped all media and retrying...')
                self.post_formatter.media.invalidate_all()
            except (SystemExit, KeyboardInterrupt) as e:
                raise SystemExit(self.feed_link, self.feed_title, self.link, self.title) from e

    async def test_format(self, user_id: int):
        sub = await db.Sub.filter(feed__link=self.feed_link, user_id=user_id).get_or_none()
        if sub is None:
            user = await db.User.get_or_none(id=user_id)
            if user is None:
                return await self.send_formatted_post(user_id=user_id)
            return await self.send_formatted_post(
                user_id=user_id,
                send_mode=user.send_mode,
                length_limit=user.length_limit,
                link_preview=user.link_preview,
                display_author=user.display_author,
                display_via=user.display_via,
                display_title=user.display_title,
                style=user.style,
                display_media=user.display_media,
                silent=not user.notify,
                display_entry_tags=user.display_entry_tags,
            )
        return await self.send_formatted_post_according_to_sub(sub=sub)
